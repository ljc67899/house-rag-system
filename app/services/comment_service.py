# -*- coding: utf-8 -*-
"""房源评论服务"""

from datetime import datetime
from loguru import logger
from sqlalchemy.orm import Session
from app.models.house import HouseComment, HouseCommentReply


class CommentService:
    def __init__(self, db: Session):
        self.db = db

    def add_comment(self, house_id: int, user_id: int, user_name: str,
                    score: float = 5.0, content: str = ""):
        """添加评论：入库时同步打情感标签，后台与统计直接读字段"""
        sentiment = None
        if (content or "").strip():
            try:
                from app.services.sentiment_service import SentimentService
                sentiment = SentimentService(self.db).analyze(content)["label"]
            except Exception as e:
                logger.warning(f"[评论] 情感标签生成失败，留空待回填: {e}")
        comment = HouseComment(
            house_id=house_id, user_id=user_id, user_name=user_name,
            score=score, content=content, sentiment=sentiment,
            create_time=datetime.now(),
        )
        self.db.add(comment)
        self.db.commit()
        self.db.refresh(comment)
        return comment

    def get_comment(self, comment_id: int):
        """按 ID 取单条评论（回复时校验归属）"""
        return self.db.query(HouseComment).filter(
            HouseComment.id == comment_id
        ).first()

    def get_reply(self, reply_id: int):
        """按 ID 取单条回复（回复楼中楼时定位被回复人）"""
        if not reply_id:
            return None
        return self.db.query(HouseCommentReply).filter(
            HouseCommentReply.id == reply_id
        ).first()

    def add_reply(self, comment_id: int, house_id: int, user_id: int,
                  user_name: str, content: str, reply_to: str = ""):
        """回复某条评论"""
        reply = HouseCommentReply(
            comment_id=comment_id, house_id=house_id, user_id=user_id,
            user_name=user_name, reply_to=(reply_to or "")[:50] or None,
            content=content, create_time=datetime.now(),
        )
        self.db.add(reply)
        self.db.commit()
        self.db.refresh(reply)
        return reply

    def get_replies_map(self, comment_ids):
        """批量取评论回复，返回 {comment_id: [reply_dict, ...]}，按时间正序"""
        ids = [int(cid) for cid in (comment_ids or []) if cid]
        if not ids:
            return {}
        rows = self.db.query(HouseCommentReply).filter(
            HouseCommentReply.comment_id.in_(ids)
        ).order_by(HouseCommentReply.create_time.asc(), HouseCommentReply.id.asc()).all()
        result = {}
        for r in rows:
            result.setdefault(int(r.comment_id), []).append({
                "id": r.id,
                "comment_id": r.comment_id,
                "user_id": r.user_id,
                "user_name": r.user_name or "匿名用户",
                "reply_to": r.reply_to or "",
                "content": r.content or "",
                "create_time": r.create_time.isoformat() if r.create_time else None,
            })
        return result

    def delete_replies_of(self, comment_ids):
        """删除若干评论下的全部回复（后台删评论时级联）"""
        ids = [int(cid) for cid in (comment_ids or []) if cid]
        if not ids:
            return 0
        deleted = self.db.query(HouseCommentReply).filter(
            HouseCommentReply.comment_id.in_(ids)
        ).delete(synchronize_session=False)
        return int(deleted or 0)

    def get_comments(self, house_id: int, limit: int = 20):
        """获取最新评论"""
        return self.db.query(HouseComment).filter(
            HouseComment.house_id == house_id
        ).order_by(HouseComment.create_time.desc()).limit(limit).all()

    def get_comments_with_stats(self, house_id: int, page: int = 1, page_size: int = 20):
        """获取评论列表 + 总数 + 平均分"""
        from sqlalchemy import func
        query = self.db.query(HouseComment).filter(
            HouseComment.house_id == house_id
        )
        total = query.count()
        avg_score = self.db.query(func.avg(HouseComment.score)).filter(
            HouseComment.house_id == house_id
        ).scalar() or 0.0
        comments = query.order_by(HouseComment.create_time.desc())\
            .offset((page - 1) * page_size).limit(page_size).all()
        return comments, total, round(float(avg_score), 2)
