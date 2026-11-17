# -*- coding: utf-8 -*-
"""站内消息服务：写入消息、拉取列表、未读数、标记已读"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.notification import UserNotification


class NotificationService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 写入 ----------

    def create(self, user_id, title: str, content: str = "", *, type: str = "comment_reply",
               house_id=None, house_title: str = "", comment_id=None,
               from_user_id=None, from_user_name: str = ""):
        """给 user_id 发一条消息；自己回复自己不发，接收人为空也不发"""
        try:
            if not user_id:
                return None
            if from_user_id and int(user_id) == int(from_user_id):
                return None
            note = UserNotification(
                user_id=int(user_id),
                type=type,
                title=(title or "")[:100],
                content=(content or "")[:500],
                house_id=house_id,
                house_title=(house_title or "")[:255],
                comment_id=comment_id,
                from_user_id=from_user_id,
                from_user_name=(from_user_name or "")[:50],
                is_read=False,
                create_time=datetime.now(),
            )
            self.db.add(note)
            self.db.commit()
            self.db.refresh(note)
            return note
        except Exception:
            # 消息只是附属功能，写失败不能影响「回复」主流程
            self.db.rollback()
            return None

    # ---------- 读取 ----------

    def unread_count(self, user_id: int) -> int:
        if not user_id:
            return 0
        return self.db.query(UserNotification).filter(
            UserNotification.user_id == user_id,
            UserNotification.is_read.is_(False),
        ).count()

    def list(self, user_id: int, page: int = 1, page_size: int = 10):
        """返回 (items, total, unread)"""
        query = self.db.query(UserNotification).filter(
            UserNotification.user_id == user_id
        )
        total = query.count()
        rows = query.order_by(UserNotification.create_time.desc(),
                              UserNotification.id.desc()) \
            .offset((page - 1) * page_size).limit(page_size).all()
        items = [{
            "id": n.id,
            "type": n.type or "comment_reply",
            "title": n.title or "",
            "content": n.content or "",
            "house_id": n.house_id,
            "house_title": n.house_title or "",
            "comment_id": n.comment_id,
            "from_user_id": n.from_user_id,
            "from_user_name": n.from_user_name or "匿名用户",
            "is_read": bool(n.is_read),
            "create_time": n.create_time.isoformat() if n.create_time else None,
        } for n in rows]
        return items, total, self.unread_count(user_id)

    # ---------- 状态变更 ----------

    def mark_read(self, user_id: int, notification_id: int) -> bool:
        note = self.db.query(UserNotification).filter(
            UserNotification.id == notification_id,
            UserNotification.user_id == user_id,   # 越权保护：只能操作自己的消息
        ).first()
        if not note:
            return False
        if not note.is_read:
            note.is_read = True
            self.db.commit()
        return True

    def mark_all_read(self, user_id: int) -> int:
        changed = self.db.query(UserNotification).filter(
            UserNotification.user_id == user_id,
            UserNotification.is_read.is_(False),
        ).update({UserNotification.is_read: True}, synchronize_session=False)
        self.db.commit()
        return int(changed or 0)
