# -*- coding: utf-8 -*-
"""模块 B：用户画像与协同过滤推荐（推荐算法升级）

- 行为信号：收藏(权重3) / 对比(权重2) / 评论(按评分 1~5) / 浏览(权重1)
- 算法一 ItemCF：基于共同行为用户计算房源相似度，推荐相似房源
- 算法二 UserCF：寻找相似用户，推荐其喜欢的房源
- 混合排序：协同分数 × 画像内容匹配 × 热度
- 冷启动：无行为用户 -> 平台热门 + 随机多样性
"""

import math
import random
from collections import defaultdict
from sqlalchemy.orm import Session

from app.models.house import (
    HouseInfo, HouseComment, HouseRecommend, HouseCompare, BrowseHistory,
)
from app.models.user import UserInfo

_F_WEIGHT = 3.0     # 收藏
_C_WEIGHT = 2.0     # 对比
_R_WEIGHT = 1.0     # 浏览

# 评论量已达十万级（每套房源都有多条评价），全量入矩阵会让 ItemCF 的用户-房源
# 组合爆炸。超过阈值时按主键取模抽样，保持人群分布均匀且计算量可控。
_COMMENT_MATRIX_FULL = 8000
_COMMENT_SAMPLE_STEP = 50


class CollaborativeService:
    """协同过滤推荐 + 用户画像"""

    def __init__(self, db: Session):
        self.db = db

    # ---------- 数据 ----------

    @staticmethod
    def _price(p):
        if not p:
            return None
        try:
            return float(str(p).replace("万元", "").replace("万", "").replace(",", ""))
        except ValueError:
            return None

    @staticmethod
    def _area(a):
        if not a:
            return None
        try:
            return float(str(a).replace("平米", "").replace("㎡", "").replace("平", ""))
        except ValueError:
            return None

    # 推荐打分/画像只需要少量字段，按列取比加载 3.8 万个 ORM 完整对象快一个数量级
    _LIGHT_COLS = (
        HouseInfo.id, HouseInfo.title, HouseInfo.price, HouseInfo.area,
        HouseInfo.direction, HouseInfo.region, HouseInfo.block, HouseInfo.address,
        HouseInfo.rooms, HouseInfo.rent_type, HouseInfo.image_url, HouseInfo.publish_date,
    )

    def _houses(self, ids=None):
        """id -> 轻量房源行；传 ids 时只查这批房源，避免全表加载"""
        if ids is None:
            return {row.id: row for row in self.db.query(*self._LIGHT_COLS).all()}
        ids = [i for i in set(ids) if i is not None]
        out = {}
        for i in range(0, len(ids), 4000):
            rows = self.db.query(*self._LIGHT_COLS).filter(
                HouseInfo.id.in_(ids[i:i + 4000])).all()
            out.update({row.id: row for row in rows})
        return out

    def _behavior_matrix(self) -> dict:
        """user_id -> {house_id: weight}"""
        matrix = defaultdict(lambda: defaultdict(float))
        # 收藏
        for fav in self.db.query(HouseRecommend).filter(HouseRecommend.score == -1).all():
            if isinstance(fav.user_id, int):
                matrix[fav.user_id][fav.house_id] += _F_WEIGHT
        # 对比
        for cmp_ in self.db.query(HouseCompare).all():
            u = self.db.query(UserInfo).filter(UserInfo.name == cmp_.user_name).first()
            if u:
                matrix[u.id][cmp_.house_id] += _C_WEIGHT
        # 评论
        comment_q = self.db.query(HouseComment)
        if self.db.query(HouseComment.id).count() > _COMMENT_MATRIX_FULL:
            comment_q = comment_q.filter(HouseComment.id % _COMMENT_SAMPLE_STEP == 0)
        for cm in comment_q.all():
            if isinstance(cm.user_id, int) and cm.score and cm.score > 0:
                matrix[cm.user_id][cm.house_id] += float(cm.score)
        # 浏览
        for bh in self.db.query(BrowseHistory).all():
            if isinstance(bh.user_id, int):
                matrix[bh.user_id][bh.house_id] += _R_WEIGHT
        return {u: dict(h) for u, h in matrix.items() if isinstance(u, int)}

    # ---------- ItemCF 相似度 ----------

    def _item_similarity(self, matrix: dict) -> dict:
        """基于共同用户计算房源余弦相似度 house_id -> {house_id: sim}

        只在「同一用户交互过的房源对」上累计共现次数：
        复杂度由 O(房源数²) 降到 O(Σ C(该用户交互数, 2))，
        评论数据量上去后也不会出现全表两两配对卡死。
        """
        item_users = defaultdict(set)
        for u, items in matrix.items():
            for hid in items:
                item_users[hid].add(u)

        cooc = defaultdict(int)
        for items in matrix.values():
            ids = list(items.keys())
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    cooc[(a, b) if a < b else (b, a)] += 1

        # 余弦相似度（共现次数 + 流行度惩罚）
        sim = defaultdict(dict)
        for (a, b), common in cooc.items():
            s = common / (math.sqrt(len(item_users[a])) * math.sqrt(len(item_users[b])))
            sim[a][b] = s
            sim[b][a] = s
        return sim

    # ---------- 用户画像 ----------

    def get_user_profile(self, user_id: int, matrix: dict = None) -> dict:
        """从用户行为聚合用户画像（推荐流程可复用已构建的行为矩阵）"""
        if matrix is None:
            matrix = self._behavior_matrix()
        user = self.db.query(UserInfo).filter(UserInfo.id == user_id).first()
        if not user:
            return {"user_id": user_id, "username": str(user_id)}

        items = matrix.get(user_id, {})
        # 只取该用户交互过的房源，不必加载全量房源
        houses = self._houses(items.keys())
        regions, rooms_n, directions, prices = [], [], [], []
        top_houses = []
        for hid, w in sorted(items.items(), key=lambda kv: -kv[1]):
            h = houses.get(hid)
            if not h:
                continue
            top_houses.append(h)
            p = self._price(h.price)
            a = self._area(h.area)
            if h.region:
                regions.append(h.region)
            if h.direction:
                directions.append(h.direction)
            if p:
                prices.append(p)
            if a:
                rooms_n.append(round(a / 45) + 1)  # 粗略按面积估算房间数

        def _top(lst, k=3):
            from collections import Counter
            return [x for x, _ in Counter(lst).most_common(k)]

        price_lo = min(prices) if prices else None
        price_hi = max(prices) if prices else None
        profile = {
            "user_id": user.id,
            "username": user.name,
            "preferred_regions": _top(regions),
            "preferred_direction": _top(directions, 2),
            "preferred_rooms": _top(rooms_n, 2),
            "price_range": [price_lo, price_hi] if prices else None,
            "behavior_count": self._behavior_counts(user_id),
            "profile_label": self._guess_label(regions, prices),
            "top_interactions": [
                {"id": h.id, "title": h.title, "region": h.region, "price": h.price}
                for h in top_houses[:5]
            ],
        }
        return profile

    def _behavior_counts(self, user_id: int) -> dict:
        return {
            "favorite": self.db.query(HouseRecommend).filter(
                HouseRecommend.user_id == user_id, HouseRecommend.score == -1).count(),
            "browse": self.db.query(BrowseHistory).filter(
                BrowseHistory.user_id == user_id).count(),
            "compare": self.db.query(HouseCompare).join(
                UserInfo, UserInfo.name == HouseCompare.user_name).filter(
                UserInfo.id == user_id).count(),
            "comment": self.db.query(HouseComment).filter(
                HouseComment.user_id == user_id).count(),
        }

    @staticmethod
    def _guess_label(regions, prices) -> str:
        labels = []
        if regions and ("西城" in regions or "东城" in regions):
            labels.append("核心城区")
        if prices:
            avg = sum(prices) / len(prices)
            if avg < 400:
                labels.append("刚需价位")
            elif avg < 900:
                labels.append("改善价位")
            else:
                labels.append("高端价位")
        return "、".join(labels) if labels else "平台用户"

    # ---------- 推荐 ----------

    def recommend(self, user_id: int, limit: int = 10) -> dict:
        """混合协同过滤推荐"""
        matrix = self._behavior_matrix()
        profile = self.get_user_profile(user_id, matrix)
        items = matrix.get(user_id, {})

        if not items:
            # 冷启动：平台热门 + 多样性
            recs = self._cold_start(limit)
            return {"strategy": "cold_start", "profile": profile, "recommendations": recs}

        # ItemCF 候选
        sim = self._item_similarity(matrix)
        interacted = set(items.keys())
        house_map = self._houses(interacted)
        cand = defaultdict(float)
        reason = {}
        for hid in interacted:
            for hid2, s in sim.get(hid, {}).items():
                if hid2 in interacted:
                    continue
                w = items[hid]
                cand[hid2] += w * s
                if reason.get(hid2) is None:
                    h = house_map.get(hid)
                    reason[hid2] = f"与您收藏过的「{h.title if h else '房源'}」相似"
        # 内容匹配加分（只为候选房源取轻量字段）
        cand_map = self._houses(cand.keys())
        for hid in list(cand):
            h = cand_map.get(hid)
            if h:
                cand[hid] *= 0.9 + 0.1 * self._content_match(profile, h)

        # UserCF 补充：相似用户喜欢的房源
        users = sorted(matrix.keys())
        target_vec = items
        similar_users = []
        for u2 in users:
            if u2 == user_id:
                continue
            s = self._cosine(target_vec, matrix[u2])
            if s > 0:
                similar_users.append((u2, s))
        similar_users.sort(key=lambda kv: -kv[1])
        for u2, s in similar_users[:8]:
            for hid, w in matrix[u2].items():
                if hid in interacted:
                    continue
                cand[hid] += s * w * 0.8
                if reason.get(hid) is None:
                    h = house_map.get(hid)
                    reason[hid] = f"与您偏好相似的用户也关注了「{h.title if h else '房源'}」"

        # 排序：只对最终命中的少量房源取完整记录（前端卡片/详情页需要全字段）
        ranked = sorted(cand.items(), key=lambda kv: -kv[1])[:limit]
        final_ids = [hid for hid, _ in ranked]
        full_rows = {
            h.id: h for h in self.db.query(HouseInfo).filter(
                HouseInfo.id.in_(final_ids)).all()
        } if final_ids else {}
        recommendations = []
        for hid, score in ranked:
            h = full_rows.get(hid)
            if not h:
                continue
            d = h.to_dict()
            d["rec_score"] = round(score, 3)
            d["rec_reason"] = reason.get(hid, "综合推荐")
            recommendations.append(d)

        strategy = "item_cf" if recommendations else "cold_start"
        if not recommendations:
            recommendations = self._cold_start(limit)
        return {"strategy": strategy, "profile": profile, "recommendations": recommendations}

    def _cold_start(self, limit: int) -> list:
        # 冷启动按最新发布排序（真实数据无 page_views）
        houses = self.db.query(HouseInfo).order_by(HouseInfo.publish_date.desc()).limit(limit * 3).all()
        rng = random.Random(20260825)
        picks = rng.sample(houses, min(limit, len(houses)))
        recs = []
        for h in picks:
            d = h.to_dict()
            d["rec_score"] = 0.0
            d["rec_reason"] = "平台热门推荐"
            recs.append(d)
        return recs

    @staticmethod
    def _cosine(a: dict, b: dict) -> float:
        common = set(a) & set(b)
        if not common:
            return 0.0
        dot = sum(a[k] * b[k] for k in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    @staticmethod
    def _content_match(profile: dict, house) -> float:
        score = 0.0
        regions = profile.get("preferred_regions") or []
        if house.region and house.region in regions:
            score += 0.5
        lo, hi = (profile.get("price_range") or [None, None])
        p = CollaborativeService._price(house.price)
        if p and lo and hi and lo <= p <= hi:
            score += 0.3
        if profile.get("preferred_direction") and house.direction in profile["preferred_direction"]:
            score += 0.2
        return score
