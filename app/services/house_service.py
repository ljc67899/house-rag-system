# -*- coding: utf-8 -*-
"""房源基础查询服务（列表/详情/热门/最新/搜索/地图）"""

import time
from typing import List, Optional, Tuple
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from app.models.house import HouseInfo
from app.utils.price_utils import parse_price_to_wan
from app.utils.regions import is_district

# 首页概览统计缓存（全量扫描价格列，避免每次进首页都扫 3.8 万行）
_OVERVIEW_CACHE = {"ts": 0.0, "data": None}
_OVERVIEW_TTL = 300


class HouseService:
    def __init__(self, db: Session):
        self.db = db

    def get_house_by_id(self, house_id: int) -> Optional[HouseInfo]:
        return self.db.query(HouseInfo).filter(HouseInfo.id == house_id).first()

    def get_house_list(
        self, page: int = 1, page_size: int = 10,
        keyword: Optional[str] = None, region: Optional[str] = None,
        block: Optional[str] = None, rooms: Optional[str] = None,
        price_min: Optional[float] = None, price_max: Optional[float] = None,
    ) -> Tuple[List[HouseInfo], int]:
        query = self.db.query(HouseInfo)
        if keyword:
            # 关键词同时匹配标题/小区/亮点/区域/板块：
            # 用户搜「朝阳区」时只匹配标题会大面积漏召回（区名多在 region 字段里）
            like = f"%{keyword}%"
            query = query.filter(
                HouseInfo.title.like(like) |
                HouseInfo.address.like(like) |
                HouseInfo.highlights.like(like) |
                HouseInfo.region.like(like) |
                HouseInfo.block.like(like)
            )
        if region:
            query = query.filter(HouseInfo.region.like(f"%{region}%"))
        if block:
            query = query.filter(HouseInfo.block.like(f"%{block}%"))
        if rooms:
            # 户型映射：一居->1室, 二居->2室, 三居->3室, 四居->4室
            # rooms 可能是列表：用户说「两居或三居」时，两种户型都算命中
            rooms_map = {
                "一居": "1室",
                "二居": "2室",
                "三居": "3室",
                "四居": "4室",
            }
            wanted = rooms if isinstance(rooms, (list, tuple, set)) else [rooms]
            room_keys = [rooms_map.get(str(r), str(r)) for r in wanted if r]
            if room_keys:
                query = query.filter(
                    or_(*[HouseInfo.rooms.like(f"%{k}%") for k in room_keys])
                )
        if price_min is not None or price_max is not None:
            # 价格筛选：统一把价格换算为"万"
            all_houses = query.all()
            filtered = []
            for h in all_houses:
                price_val = parse_price_to_wan(h.price)
                if price_val is None:
                    continue
                if price_min is not None and price_val < price_min:
                    continue
                if price_max is not None and price_val > price_max:
                    continue
                filtered.append(h)
            total = len(filtered)
            start = (page - 1) * page_size
            houses = filtered[start:start + page_size]
            return houses, total

        total = query.count()
        houses = query.order_by(HouseInfo.publish_date.desc())\
            .offset((page - 1) * page_size).limit(page_size).all()
        return houses, total

    def get_hot_houses(self, limit: int = 6) -> List[HouseInfo]:
        # 真实数据无 page_views，按发布时间热度展示
        return self.db.query(HouseInfo).order_by(HouseInfo.publish_date.desc()).limit(limit).all()

    def get_new_houses(self, limit: int = 6) -> List[HouseInfo]:
        return self.db.query(HouseInfo).order_by(HouseInfo.publish_date.desc()).limit(limit).all()

    def increment_views(self, house_id: int) -> bool:
        # 真实数据无 page_views 字段，浏览量由前端埋点或独立统计表维护
        return self.get_house_by_id(house_id) is not None

    def search_houses_by_keyword(self, keyword: str, limit: int = 50) -> List[HouseInfo]:
        return self.db.query(HouseInfo).filter(
            HouseInfo.title.like(f"%{keyword}%") |
            HouseInfo.address.like(f"%{keyword}%") |
            HouseInfo.region.like(f"%{keyword}%") |
            HouseInfo.block.like(f"%{keyword}%")
        ).limit(limit).all()

    def get_all_houses_for_rag(self, limit: int = 1000) -> List[HouseInfo]:
        return self.db.query(HouseInfo).limit(limit).all()

    # ---------- 房源管理（后台增删改） ----------

    @staticmethod
    def _allowed_fields() -> set:
        return set(HouseInfo.model_fields.keys()) - {"id", "created_at", "updated_at"}

    def create_house(self, payload: dict) -> HouseInfo:
        """新增房源"""
        allowed = self._allowed_fields()
        data = {k: v for k, v in payload.items() if k in allowed and v is not None}
        house = HouseInfo(**data)
        self.db.add(house)
        self.db.commit()
        self.db.refresh(house)
        return house

    def update_house(self, house_id: int, payload: dict) -> Optional[HouseInfo]:
        """更新房源（只覆盖传入的非空字段）"""
        house = self.get_house_by_id(house_id)
        if not house:
            return None
        allowed = self._allowed_fields()
        for key, value in payload.items():
            if key in allowed and value is not None:
                setattr(house, key, value)
        self.db.commit()
        self.db.refresh(house)
        return house

    def delete_house(self, house_id: int) -> bool:
        """删除房源"""
        house = self.get_house_by_id(house_id)
        if not house:
            return False
        self.db.delete(house)
        self.db.commit()
        return True

    def get_all_houses_for_map(self, region: str = None, limit: int = 800, offset: int = 0):
        """获取房源用于地图展示（轻量字段）

        房源 3.8 万条，若简单地 order_by(id).limit(3000) 截取，
        只会取到种子数据前半段（集中在少数几个区），地图上看起来"全城就几个点"。
        超出上限时改为按 id 等间距抽样，覆盖整个 id 区间，从而覆盖全部区域。
        """
        MAX_MAP_HOUSES = 6000
        limit = max(1, min(limit or MAX_MAP_HOUSES, MAX_MAP_HOUSES))
        query = self.db.query(HouseInfo).filter(
            HouseInfo.longitude.isnot(None),
            HouseInfo.longitude != "",
            HouseInfo.latitude.isnot(None),
            HouseInfo.latitude != "",
        )
        if region:
            # 优先精确匹配，命中索引；无结果再回退模糊匹配
            exact_count = query.filter(HouseInfo.region == region).count()
            if exact_count > 0:
                query = query.filter(HouseInfo.region == region)
            else:
                query = query.filter(HouseInfo.region.like(f"%{region}%"))
        total = query.count()
        if total > limit:
            all_ids = [row[0] for row in query.with_entities(HouseInfo.id).order_by(HouseInfo.id).all()]
            # 用与总数互质的步长做“伪随机均匀抽样”：既覆盖全部区域，列表也不会按区域连成一片
            n = len(all_ids)
            stride = 7919
            while n > 1 and n % stride == 0:
                stride -= 2
            picked, seen, i = [], set(), 0
            while len(picked) < limit and i < n:
                hid = all_ids[(i * stride) % n]
                if hid not in seen:
                    seen.add(hid)
                    picked.append(hid)
                i += 1
            rows = {}
            for i in range(0, len(picked), 4000):
                for h in self.db.query(HouseInfo).filter(
                        HouseInfo.id.in_(picked[i:i + 4000])).all():
                    rows[h.id] = h
            # 按抽样顺序返回：数据库会按 id 升序返回，直接使用会让列表按区域连成一片
            houses = [rows[hid] for hid in picked if hid in rows]
        else:
            houses = query.order_by(HouseInfo.id).limit(limit).offset(offset).all()
        result = []
        for h in houses:
            result.append({
                "id": h.id,
                "title": h.title,
                "price": h.price,
                "rooms": h.rooms,
                "area": h.area,
                "address": h.address,
                "region": h.region,
                "block": h.block,
                "direction": h.direction,
                "rent_type": h.rent_type,
                "longitude": h.longitude,
                "latitude": h.latitude,
                "image_url": h.image_url,
            })
        return result, total

    def get_overview_stats(self) -> dict:
        """首页概览：总房源数 / 覆盖行政区数 / 平均总价（万）。

        一律走全量口径——前端此前用“首页前 50 条”估算，会随排序变化而跳动。
        """
        now = time.time()
        if _OVERVIEW_CACHE["data"] and (now - _OVERVIEW_CACHE["ts"]) < _OVERVIEW_TTL:
            return _OVERVIEW_CACHE["data"]

        total = self.db.query(func.count(HouseInfo.id)).scalar() or 0

        prices = [p for (p,) in self.db.query(HouseInfo.price).all()]
        price_wan = [
            v for v in (parse_price_to_wan(p) for p in prices) if v and v > 0
        ]

        region_rows = self.db.query(HouseInfo.region).distinct().all()
        region_count = sum(1 for (r,) in region_rows if is_district(r))

        data = {
            "total": int(total),
            "region_count": region_count,
            "avg_price": round(sum(price_wan) / len(price_wan), 1) if price_wan else 0,
        }
        _OVERVIEW_CACHE["ts"] = now
        _OVERVIEW_CACHE["data"] = data
        return data

    def list_regions(self) -> List[dict]:
        """区域列表（按房源数排序），供地图找房/筛选下拉使用

        只返回北京行政区县。历史上 region 混入过城市级脏值（如 "北京"），
        这类值直接过滤，避免它出现在下拉框、也避免被首页统计当成一个区域。
        """
        from app.utils.regions import is_district

        rows = self.db.query(
            HouseInfo.region, func.count(HouseInfo.id).label("cnt")
        ).filter(
            HouseInfo.region.isnot(None), HouseInfo.region != ""
        ).group_by(HouseInfo.region).order_by(func.count(HouseInfo.id).desc()).all()
        return [
            {"name": r[0].strip(), "count": r[1]}
            for r in rows if is_district(r[0])
        ]

    # ===== 门面委托方法：保持 API 层调用方式不变，业务逻辑按职责拆分到各子服务 =====

    # ---- 智能推荐 ----
    def get_recommendations(self, user_id: int = 0, limit: int = 20):
        from app.services.recommend_service import RecommendService
        return RecommendService(self.db).get_recommendations(user_id, limit)

    # ---- 房价估算 ----
    def estimate_price(self, rooms="2室1厅", area=80, region="朝阳区",
                       direction="南北", decoration="精装修", floor="中层"):
        from app.services.estimate_service import EstimateService
        return EstimateService(self.db).estimate_price(
            rooms=rooms, area=area, region=region,
            direction=direction, decoration=decoration, floor=floor)

    # ---- 收藏 ----
    def get_favorites(self, user_id: int = 0, page: int = 1, page_size: int = 20):
        from app.services.favorite_service import FavoriteService
        return FavoriteService(self.db).get_favorites(user_id, page, page_size)

    def add_favorite(self, user_id: int = 0, house_id: int = 0):
        from app.services.favorite_service import FavoriteService
        return FavoriteService(self.db).add_favorite(user_id, house_id)

    def remove_favorite(self, user_id: int = 0, house_id: int = 0):
        from app.services.favorite_service import FavoriteService
        return FavoriteService(self.db).remove_favorite(user_id, house_id)

    def is_favorited(self, user_id: int = 0, house_id: int = 0):
        from app.services.favorite_service import FavoriteService
        return FavoriteService(self.db).is_favorited(user_id, house_id)

    # ---- 对比 ----
    def get_compare_list(self, user_name: str = "guest"):
        from app.services.compare_service import CompareService
        return CompareService(self.db).get_compare_list(user_name)

    def get_compare_verdict(self, items: list):
        from app.services.compare_service import CompareService
        return CompareService(self.db).build_verdict(items)

    def add_compare(self, user_name: str = "guest", house_id: int = 0):
        from app.services.compare_service import CompareService
        return CompareService(self.db).add_compare(user_name, house_id)

    def remove_compare(self, user_name: str = "guest", house_id: int = 0):
        from app.services.compare_service import CompareService
        return CompareService(self.db).remove_compare(user_name, house_id)

    def clear_compare(self, user_name: str = "guest"):
        from app.services.compare_service import CompareService
        return CompareService(self.db).clear_compare(user_name)

    # ---- 浏览历史 ----
    def get_browse_history(self, user_id: int = 0, page: int = 1, page_size: int = 20):
        from app.services.browse_history_service import BrowseHistoryService
        return BrowseHistoryService(self.db).get_browse_history(user_id, page, page_size)

    def clear_browse_history(self, user_id: int = 0):
        from app.services.browse_history_service import BrowseHistoryService
        return BrowseHistoryService(self.db).clear_browse_history(user_id)

    def remove_browse_history(self, user_id: int = 0, house_id: int = 0):
        from app.services.browse_history_service import BrowseHistoryService
        return BrowseHistoryService(self.db).remove_browse_history(user_id, house_id)

    def add_browse_history(self, user_id: int = 0, house_id: int = 0):
        from app.services.browse_history_service import BrowseHistoryService
        return BrowseHistoryService(self.db).add_browse_history(user_id, house_id)

    # ---- 评论 ----
    def get_comments_with_stats(self, house_id: int = 0, page: int = 1, page_size: int = 20):
        from app.services.comment_service import CommentService
        return CommentService(self.db).get_comments_with_stats(house_id, page, page_size)

    def add_comment(self, house_id: int = 0, user_id: int = 0,
                    user_name: str = "匿名用户", score: float = 5.0, content: str = ""):
        from app.services.comment_service import CommentService
        return CommentService(self.db).add_comment(house_id, user_id, user_name, score, content)
