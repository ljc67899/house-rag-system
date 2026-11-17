# -*- coding: utf-8 -*-
"""区域行情统计（估算页与对比页共用的"真实基准"数据源）

存在的意义：前端此前用「估值 × 0.95」「估值 / 500」这类算式伪造同小区均价、
区域排名，属于假数据。这个模块只做一件事——从数据库真算区域单价分布，
供估算页做分位定位、对比页做"比区域均价高/低 x%"的结论。

实现要点：
    1. 只查 price/area 两列，不加载 ORM 实体，避免 4000+ 行实体化开销；
    2. 区域级结果缓存 10 分钟，重复估算 / 多次对比不会反复全表扫描；
    3. 百分位用线性插值，样本不足时返回 None，前端显示"数据不足"而不是编一个数。
"""

import time
from statistics import median
from typing import Optional

from sqlalchemy.orm import Session

from app.models.house import HouseInfo
from app.utils.price_utils import parse_area_to_sqm, parse_price_to_wan

_CACHE_TTL = 600
_CACHE: dict = {}


def _percentile(sorted_values: list, pct: float) -> Optional[float]:
    """线性插值百分位（pct 取 0~100）"""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * pct / 100.0
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


class MarketStatsService:
    def __init__(self, db: Session):
        self.db = db

    def get_region_stats(self, region: str) -> dict:
        """返回某区域的真实行情：单价均值/中位数/四分位、总价中位数、样本数"""
        key = (region or "").strip()
        entry = _CACHE.get(key)
        if entry and time.time() - entry["ts"] < _CACHE_TTL:
            return entry["data"]

        rows = (
            self.db.query(HouseInfo.price, HouseInfo.area)
            .filter(HouseInfo.region == key)
            .all()
        ) if key else []

        units, totals = [], []
        for price, area in rows:
            p = parse_price_to_wan(price)
            a = parse_area_to_sqm(area)
            if p and p > 0 and a and a > 0:
                units.append(p / a)
                totals.append(p)

        units.sort()
        totals.sort()
        data = {
            "region": key,
            "sample_count": len(units),
            "avg_unit_price": round(sum(units) / len(units), 2) if units else None,
            "median_unit_price": round(median(units), 2) if units else None,
            "unit_p25": round(_percentile(units, 25), 2) if units else None,
            "unit_p75": round(_percentile(units, 75), 2) if units else None,
            "median_total_price": round(median(totals), 1) if totals else None,
            "total_p25": round(_percentile(totals, 25), 1) if totals else None,
            "total_p75": round(_percentile(totals, 75), 1) if totals else None,
            "_units": units,  # 内部用，返回给前端前需剔除
        }
        _CACHE[key] = {"ts": time.time(), "data": data}
        return data

    def percentile_of(self, region: str, unit_price: Optional[float]) -> Optional[int]:
        """给定单价，算它在所属区域房源中的分位（0~100，越大越贵）"""
        if not unit_price:
            return None
        units = self.get_region_stats(region).get("_units") or []
        if len(units) < 10:
            return None
        below = sum(1 for u in units if u <= unit_price)
        return int(round(below * 100 / len(units)))

    @staticmethod
    def public(stats: dict) -> dict:
        """去掉内部字段，直接返回给前端"""
        return {k: v for k, v in stats.items() if not k.startswith("_")}


def get_market_stats_service(db: Session) -> MarketStatsService:
    return MarketStatsService(db)


__all__ = ["MarketStatsService", "get_market_stats_service"]
