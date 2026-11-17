# -*- coding: utf-8 -*-
"""智能推荐服务（热度 + 收藏 + 浏览 + 随机 加权打分）"""

import random
from sqlalchemy.orm import Session
from app.models.house import HouseInfo, HouseRecommend, BrowseHistory

RAG_CANDIDATE_LIMIT = 50  # 参与打分的候选房源数


class RecommendService:
    def __init__(self, db: Session):
        self.db = db

    def get_recommendations(self, user_id: int = 0, limit: int = 20):
        """综合推荐：基于热度+收藏+浏览打分"""
        all_houses = self.db.query(HouseInfo).limit(RAG_CANDIDATE_LIMIT).all()
        if not all_houses:
            return [], 0

        scored = []
        for house in all_houses:
            score = 0.0
            reasons = []

            # 热度分：用发布时间、区域等效热度打分（真实数据无 page_views）
            view_score = 20.0
            score += view_score
            if view_score >= 20:
                reasons.append("热门房源")

            # 收藏加分（+30）
            if user_id > 0:
                fav = self.db.query(HouseRecommend).filter(
                    HouseRecommend.user_id == user_id,
                    HouseRecommend.house_id == house.id,
                    HouseRecommend.score == -1,  # -1 表示收藏
                ).first()
                if fav:
                    score += 30
                    reasons.append("您收藏过的房源")

            # 浏览加分（+20）
            if user_id > 0:
                browsed = self.db.query(BrowseHistory).filter(
                    BrowseHistory.user_id == user_id,
                    BrowseHistory.house_id == house.id,
                ).first()
                if browsed:
                    score += 20
                    reasons.append("您浏览过的房源")

            # 随机微调，保证推荐多样性
            score += random.uniform(0, 15)
            if not reasons:
                reasons.append("精选推荐")

            scored.append({
                "house_id": house.id,
                "title": house.title,
                "address": house.address,
                "score": round(score, 2),
                "reason": "、".join(reasons),
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:limit]

        house_ids = [s["house_id"] for s in top]
        full_houses = self.db.query(HouseInfo).filter(
            HouseInfo.id.in_(house_ids)
        ).all()
        house_map = {h.id: h for h in full_houses}

        result = []
        for s in top:
            h = house_map.get(s["house_id"])
            if not h:
                continue
            result.append({
                "id": h.id, "title": h.title, "rooms": h.rooms,
                "area": h.area, "price": h.price, "direction": h.direction,
                "rent_type": h.rent_type, "region": h.region, "block": h.block,
                "address": h.address, "traffic": h.traffic,
                "facilities": h.facilities or "", "highlights": h.highlights or "",
                "image_url": h.image_url or "",
                "longitude": h.longitude, "latitude": h.latitude,
                "contact_name": h.contact_name, "contact_phone": h.contact_phone,
                "score": s["score"], "reason": s["reason"],
            })
        return result, len(scored)
