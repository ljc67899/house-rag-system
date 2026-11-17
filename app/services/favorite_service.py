# -*- coding: utf-8 -*-
"""房源收藏服务"""

from sqlalchemy.orm import Session
from app.models.house import HouseInfo, HouseRecommend


class FavoriteService:
    def __init__(self, db: Session):
        self.db = db

    def _get_house(self, house_id: int) -> HouseInfo:
        return self.db.query(HouseInfo).filter(HouseInfo.id == house_id).first()

    def add_favorite(self, user_id: int, house_id: int) -> bool:
        """添加收藏"""
        house = self._get_house(house_id)
        if not house:
            return False
        existing = self.db.query(HouseRecommend).filter(
            HouseRecommend.user_id == user_id,
            HouseRecommend.house_id == house_id,
            HouseRecommend.score == -1,  # -1 means favorite
        ).first()
        if existing:
            return True
        fav = HouseRecommend(
            user_id=user_id, house_id=house_id,
            title=house.title, address=house.address,
            block=house.block, score=-1,
        )
        self.db.add(fav)
        self.db.commit()
        return True

    def remove_favorite(self, user_id: int, house_id: int) -> bool:
        """取消收藏"""
        fav = self.db.query(HouseRecommend).filter(
            HouseRecommend.user_id == user_id,
            HouseRecommend.house_id == house_id,
            HouseRecommend.score == -1,
        ).first()
        if fav:
            self.db.delete(fav)
            self.db.commit()
            return True
        return False

    def get_favorites(self, user_id: int, page: int = 1, page_size: int = 20):
        """获取收藏列表（富化为完整房源信息）"""
        query = self.db.query(HouseRecommend).filter(
            HouseRecommend.user_id == user_id,
            HouseRecommend.score == -1,
        )
        total = query.count()
        favs = query.order_by(HouseRecommend.id.desc())\
            .offset((page - 1) * page_size).limit(page_size).all()
        result = []
        for fav in favs:
            house = self._get_house(fav.house_id)
            if house:
                d = house.to_dict()
                d["fav_id"] = fav.id
                d["add_time"] = None
                result.append(d)
        return result, total

    def is_favorited(self, user_id: int, house_id: int) -> bool:
        """检查是否已收藏"""
        return self.db.query(HouseRecommend).filter(
            HouseRecommend.user_id == user_id,
            HouseRecommend.house_id == house_id,
            HouseRecommend.score == -1,
        ).first() is not None
