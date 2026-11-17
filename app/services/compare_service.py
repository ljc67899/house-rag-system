# -*- coding: utf-8 -*-
"""房源对比服务

除了原始字段，这里额外算三类「能帮用户做决定」的派生数据：
    1. 单价 / 月供：把"XXX万"和"XXX平米"从字符串变成可比较的数值；
    2. 相对区域均价：告诉用户这套是贵还是便宜，而不是只给绝对值；
    3. 一句话结论：从 N 套里指出哪套最值得，以及它的代价是什么。
结论只使用能算出来的事实（单价、总价、满五、建成年份），不引入主观评分，
避免出现"综合分 8.7"这种无法解释的数字。
"""

import re

from sqlalchemy.orm import Session

from app.models.house import HouseInfo, HouseCompare
from app.services.market_stats import MarketStatsService
from app.utils.price_utils import monthly_payment, parse_area_to_sqm, parse_price_to_wan


class CompareService:
    MAX_COMPARE = 4  # 最多同时对比 4 套

    def __init__(self, db: Session):
        self.db = db

    def _get_house(self, house_id: int) -> HouseInfo:
        return self.db.query(HouseInfo).filter(HouseInfo.id == house_id).first()

    def add_compare(self, user_name: str, house_id: int) -> bool:
        """加入对比（超过上限返回 False）"""
        house = self._get_house(house_id)
        if not house:
            return False
        count = self.db.query(HouseCompare).filter(
            HouseCompare.user_name == user_name
        ).count()
        if count >= self.MAX_COMPARE:
            return False
        existing = self.db.query(HouseCompare).filter(
            HouseCompare.user_name == user_name,
            HouseCompare.house_id == house_id,
        ).first()
        if existing:
            return True
        cmp = HouseCompare(user_name=user_name, house_id=house_id)
        self.db.add(cmp)
        self.db.commit()
        return True

    def remove_compare(self, user_name: str, house_id: int) -> bool:
        cmp = self.db.query(HouseCompare).filter(
            HouseCompare.user_name == user_name,
            HouseCompare.house_id == house_id,
        ).first()
        if cmp:
            self.db.delete(cmp)
            self.db.commit()
            return True
        return False

    def clear_compare(self, user_name: str):
        """清空对比列表"""
        self.db.query(HouseCompare).filter(
            HouseCompare.user_name == user_name
        ).delete()
        self.db.commit()

    def get_compare_list(self, user_name: str):
        """获取对比列表（返回完整字段，用于详细对比）"""
        cmps = self.db.query(HouseCompare).filter(
            HouseCompare.user_name == user_name
        ).order_by(HouseCompare.add_time.desc()).all()
        result = []
        for cmp in cmps:
            house = self._get_house(cmp.house_id)
            if house:
                result.append({
                    "id": cmp.house_id,
                    "title": house.title,
                    "price": house.price,
                    "rooms": house.rooms,
                    "area": house.area,
                    "direction": house.direction,
                    "rent_type": house.rent_type,
                    "region": house.region,
                    "block": house.block,
                    "address": house.address,
                    "traffic": house.traffic,
                    "floor": house.floor,
                    "build_year": house.build_year,
                    "decoration": house.decoration,
                    "house_type": house.house_type,
                    "facilities": house.facilities,
                    "highlights": house.highlights,
                    "matching": house.matching,
                    "description": house.description,
                    "image_url": house.image_url,
                    "longitude": house.longitude,
                    "latitude": house.latitude,
                    "contact_name": house.contact_name,
                    "contact_phone": house.contact_phone,
                    "publish_date": house.publish_date,
                })
        self._enrich(result)
        return result, len(result)

    # ---------- 派生指标 ----------

    def _enrich(self, items: list) -> None:
        """补充单价、月供、相对区域均价，全部来自真实数据"""
        if not items:
            return
        market = MarketStatsService(self.db)
        for item in items:
            price = parse_price_to_wan(item.get("price"))
            area = parse_area_to_sqm(item.get("area"))
            item["price_wan"] = round(price, 1) if price else None
            item["area_sqm"] = round(area, 1) if area else None
            item["per_unit"] = round(price / area, 2) if price and area else None
            item["monthly_payment"] = monthly_payment(price) if price else None
            item["build_year_num"] = self._parse_year(item.get("build_year"))

            stats = market.get_region_stats(item.get("region") or "")
            avg_unit = stats.get("avg_unit_price")
            item["region_avg_unit"] = avg_unit
            if avg_unit and item["per_unit"]:
                item["vs_region_pct"] = round((item["per_unit"] - avg_unit) / avg_unit * 100, 1)
            else:
                item["vs_region_pct"] = None

    @staticmethod
    def _parse_year(value):
        if not value:
            return None
        match = re.search(r"(19|20)\d{2}", str(value))
        return int(match.group()) if match else None

    # ---------- 一句话结论 ----------

    def build_verdict(self, items: list):
        """从对比项里挑出最值得的一套，并说明理由与代价。

        只陈述可计算事实：单价最低、低于区域均价、总价最低、满五、建成年份最新。
        若数据不足以支撑结论则返回 None，前端不显示结论条（宁可不给，也不编）。
        """
        if not items or len(items) < 2:
            return None

        priced = [i for i in items if i.get("per_unit")]
        if not priced:
            return None
        best = min(priced, key=lambda i: i["per_unit"])

        reasons = [f"单价 {best['per_unit']:g} 万/㎡"]
        pct = best.get("vs_region_pct")
        if pct is not None and pct < 0:
            reasons.append(f"比{best.get('region') or '区域'}均价低 {abs(pct):g}%")

        cheapest = min(
            [i for i in items if i.get("price_wan")],
            key=lambda i: i["price_wan"],
            default=None,
        )
        if cheapest and cheapest["id"] == best["id"]:
            reasons.append(f"总价最低 {best['price_wan']:g} 万")

        if "满五" in (best.get("highlights") or "") or "满五年" in (best.get("highlights") or ""):
            reasons.append("满五，税费更省")

        newest = max(
            [i for i in items if i.get("build_year_num")],
            key=lambda i: i["build_year_num"],
            default=None,
        )
        if newest and newest["id"] == best["id"]:
            reasons.append(f"建成年份最新（{best['build_year_num']} 年）")

        # 代价：面积是否明显小于其他户型
        tradeoffs = []
        areas = [i for i in items if i.get("area_sqm")]
        if areas and best.get("area_sqm"):
            largest = max(areas, key=lambda i: i["area_sqm"])
            if largest["id"] != best["id"]:
                gap = round(largest["area_sqm"] - best["area_sqm"], 1)
                if gap > 0:
                    tradeoffs.append(
                        f"比面积最大的「{largest.get('block') or largest['title']}」少 {gap:g}㎡"
                    )
        tradeoff = "；".join(tradeoffs) if tradeoffs else None

        return {
            "recommended_id": best["id"],
            "headline": f"推荐「{best.get('block') or best.get('title')}」",
            "reasons": reasons[:4],
            "tradeoff": tradeoff,
            "basis": "依据单价、总价与建成年份等可核验字段计算，不含主观评分",
        }
