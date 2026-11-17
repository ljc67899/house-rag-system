# -*- coding: utf-8 -*-
"""浏览历史服务"""

from datetime import datetime
from sqlalchemy.orm import Session
from app.models.house import BrowseHistory, HouseInfo

MAX_HISTORY = 50  # 每个用户最多保留 50 条浏览记录


class BrowseHistoryService:
    def __init__(self, db: Session):
        self.db = db

    def add_browse_history(self, user_id: int, house_id: int):
        """记录浏览历史（去重 + 超限清理最旧记录）"""
        house = self.db.query(HouseInfo).filter(HouseInfo.id == house_id).first()
        if not house:
            return None
        # 删除重复记录，保证同一房源只保留最新一条
        self.db.query(BrowseHistory).filter(
            BrowseHistory.user_id == user_id,
            BrowseHistory.house_id == house_id,
        ).delete()
        history = BrowseHistory(
            user_id=user_id, house_id=house_id,
            house_title=house.title, house_price=house.price,
            house_address=house.address, view_time=datetime.now(),
        )
        self.db.add(history)
        # 超过上限时删除最旧的记录
        count = self.db.query(BrowseHistory).filter(
            BrowseHistory.user_id == user_id
        ).count()
        if count > MAX_HISTORY:
            old_records = self.db.query(BrowseHistory).filter(
                BrowseHistory.user_id == user_id
            ).order_by(BrowseHistory.view_time.asc())\
                .limit(count - MAX_HISTORY).all()
            for r in old_records:
                self.db.delete(r)
        self.db.commit()
        self.db.refresh(history)
        return history

    def get_browse_history(self, user_id: int, page: int = 1, page_size: int = 20):
        """获取浏览历史（分页，按时间倒序）

        浏览记录表只存了少量快照字段，前端需要标题 / 户型 / 面积 / 图片，
        所以这里把对应房源信息合并进来，返回可直接渲染的字典列表。
        """
        query = self.db.query(BrowseHistory).filter(
            BrowseHistory.user_id == user_id
        )
        total = query.count()
        records = query.order_by(BrowseHistory.view_time.desc())\
            .offset((page - 1) * page_size).limit(page_size).all()

        result = []
        for rec in records:
            view_time = rec.view_time.strftime("%Y-%m-%d %H:%M") if rec.view_time else ""
            house = self.db.query(HouseInfo).filter(HouseInfo.id == rec.house_id).first()
            if house:
                d = house.to_dict()
                d["history_id"] = rec.id
                d["browse_time"] = view_time
                result.append(d)
            else:
                # 房源已下架：用记录里的快照字段兜底，避免整条记录空白
                result.append({
                    "id": rec.house_id,
                    "title": rec.house_title or "房源已下架",
                    "price": rec.house_price,
                    "address": rec.house_address,
                    "image_url": None,
                    "rooms": None,
                    "area": None,
                    "history_id": rec.id,
                    "browse_time": view_time,
                })
        return result, total

    def remove_browse_history(self, user_id: int, house_id: int) -> bool:
        """删除单条浏览记录"""
        deleted = self.db.query(BrowseHistory).filter(
            BrowseHistory.user_id == user_id,
            BrowseHistory.house_id == house_id,
        ).delete()
        self.db.commit()
        return deleted > 0

    def clear_browse_history(self, user_id: int) -> bool:
        """清空浏览历史"""
        self.db.query(BrowseHistory).filter(
            BrowseHistory.user_id == user_id
        ).delete()
        self.db.commit()
        return True
