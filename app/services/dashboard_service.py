# -*- coding: utf-8 -*-
"""模块 C：房产数据可视化分析大屏（统计聚合服务）

从 house_info 聚合多维统计指标，供前端 ECharts 大屏渲染。
"""

import re
import time
from collections import defaultdict, Counter
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, Float

from app.models.house import HouseInfo
from app.models.trend import HousePriceTrend
from app.utils.regions import is_district

_cache = {"ts": 0, "data": None}
_CACHE_TTL = 300  # 5 分钟缓存
_block_cache = {}  # 板块行情：按区域缓存


class DashboardService:
    """大屏统计聚合"""

    def __init__(self, db: Session):
        self.db = db

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

    @staticmethod
    def _rooms(r):
        s = str(r or "")
        def _n(k):
            m = re.search(r"(\d+)" + k, s)
            return int(m.group(1)) if m else 0
        return _n("室"), _n("厅"), _n("卫")

    def stats(self) -> dict:
        # 缓存命中
        now = time.time()
        if _cache["data"] and (now - _cache["ts"]) < _CACHE_TTL:
            return _cache["data"]

        # 只查需要的列（不加载完整 ORM 对象，大幅减少内存和序列化开销）
        rows = self.db.query(
            HouseInfo.price, HouseInfo.area, HouseInfo.rooms,
            HouseInfo.region, HouseInfo.direction, HouseInfo.address,
        ).all()

        region_price = defaultdict(list)
        region_count = Counter()
        region_unit = defaultdict(list)     # 区域单价，气泡图/雷达图用
        region_area = defaultdict(list)     # 区域平均面积
        region_room = defaultdict(Counter)  # 区域 × 户型结构
        region_bucket = defaultdict(Counter)  # 区域 × 价格段，热力图用
        room_dist = Counter()
        price_bucket = Counter()
        direction_dist = Counter()
        area_bucket = Counter()
        block_price = defaultdict(list)
        total_price = []
        unit_prices = []

        for price_str, area_str, rooms_str, region, direction, address in rows:
            p = self._price(price_str)
            a = self._area(area_str)
            room, _, _ = self._rooms(rooms_str)
            if p and a:
                total_price.append(p)
                unit_prices.append(p / a)
            # 区域维度只统计合法行政区县，避免城市级脏值（如 "北京"）被算成一个区
            if region and is_district(region):
                if p:
                    region_price[region].append(p)
                region_count[region] += 1
                if p and a:
                    region_unit[region].append(p / a)
                if a:
                    region_area[region].append(a)
                if room:
                    region_room[region][f"{room}室"] += 1
                if p:
                    region_bucket[region][self._price_bucket(p)] += 1
            if room:
                room_dist[f"{room}室"] += 1
            if p:
                price_bucket[self._price_bucket(p)] += 1
            if direction:
                direction_dist[self._direction_group(direction)] += 1
            if a:
                area_bucket[self._area_bucket(a)] += 1
            if address and p:
                block_price[address].append(p)

        def _top(counter_or_dict, n=12, reverse=True):
            return sorted(counter_or_dict.items(), key=lambda kv: kv[1], reverse=reverse)[:n]

        regions = _top({k: sum(v) / len(v) for k, v in region_price.items() if v}, 12)
        block_avg = sorted(
            ({k: sum(v) / len(v) for k, v in block_price.items() if len(v) >= 3}).items(),
            key=lambda kv: -kv[1])[:10]

        # 区域多维：均价 / 在挂套数 / 平均面积（气泡图）
        region_scatter = []
        for name, cnt in region_count.items():
            prices = region_price.get(name) or []
            if not prices:
                continue
            areas = region_area.get(name) or []
            units = region_unit.get(name) or []
            region_scatter.append({
                "name": name,
                "value": [
                    round(sum(prices) / len(prices), 2),
                    cnt,
                    round(sum(areas) / len(areas), 1) if areas else 0,
                ],
                "unit_price": round(sum(units) / len(units), 2) if units else 0,
            })
        region_scatter.sort(key=lambda x: -x["value"][0])

        # 区域 × 价格段 矩阵（热力图）：看各区成交集中在哪个价位段
        price_labels = ["<200万", "200-400万", "400-700万", "700-1000万", "1000-1500万", ">1500万"]
        # 反序：ECharts 分类 y 轴第一项在最下方，反序后均价最高的区排在最上面
        heat_regions = [x["name"] for x in region_scatter[:10]][::-1]
        heat_data = []
        for yi, name in enumerate(heat_regions):
            buckets = region_bucket.get(name) or {}
            for xi, label in enumerate(price_labels):
                v = buckets.get(label, 0)
                if v:
                    heat_data.append([xi, yi, v])

        # 区域 × 户型结构（100% 堆叠条）：各区户型偏好差异
        # 只保留占比最高的 6 类户型，其余合并为「其他」，否则图例太长、小切片看不见
        room_categories = [k for k, _ in _top(room_dist, 6)]
        region_room_struct = []
        for name, _ in _top(region_count, 8):
            rooms = region_room.get(name) or {}
            room_total = sum(rooms.values())
            if not room_total:
                continue
            grouped = {k: rooms.get(k, 0) for k in room_categories}
            other = room_total - sum(grouped.values())
            if other > 0:
                grouped["其他"] = other
            region_room_struct.append({
                "name": name,
                "total": room_total,
                "rooms": grouped,
            })
        if any("其他" in x["rooms"] for x in region_room_struct):
            room_categories.append("其他")

        total = len(rows)
        avg_price = sum(total_price) / len(total_price) if total_price else 0
        avg_unit = sum(unit_prices) / len(unit_prices) if unit_prices else 0

        data = {
            "overview": {
                "total_houses": total,
                "avg_price": round(avg_price, 2),
                "avg_unit_price": round(avg_unit, 2),
                "price_max": round(max(total_price), 2) if total_price else 0,
                "price_min": round(min(total_price), 2) if total_price else 0,
                "region_count": len(region_count),
            },
            "region_avg_price": [{"name": k, "value": round(v, 2)} for k, v in regions],
            "region_count": [{"name": k, "value": v} for k, v in _top(region_count, 12)],
            "room_dist": [{"name": k, "value": v} for k, v in _top(room_dist, 8)],
            # 价格段按自然顺序输出（低价→高价），不要按数量排序，否则横轴看着像乱的
            "price_dist": [{"name": k, "value": price_bucket.get(k, 0)} for k in price_labels],
            "direction_dist": [{"name": k, "value": v} for k, v in _top(direction_dist, 6)],
            "area_dist": [{"name": k, "value": v} for k, v in _top(area_bucket, 8)],
            "top_blocks": [{"name": k, "value": round(v, 2)} for k, v in block_avg],
            # 图表多样性：气泡图 / 热力图 / 100% 堆叠条 所需的多维聚合
            "region_scatter": region_scatter,
            "region_price_heatmap": {
                "x": price_labels,
                "y": heat_regions,
                "data": heat_data,
            },
            "room_categories": room_categories,
            "region_room_struct": region_room_struct,
            # 各区最新月度环比（只读 trend 表相邻两月，不触发 LSTM）
            "region_mom": self._region_mom(),
        }
        _cache["data"] = data
        _cache["ts"] = now
        return data

    def _region_mom(self) -> list:
        """各区最新月度挂牌均价环比，供区域对比图标注涨跌"""
        rows = self.db.query(
            HousePriceTrend.region, HousePriceTrend.month, HousePriceTrend.avg_price
        ).all()
        hist = defaultdict(list)
        for region, month, price in rows:
            if region and is_district(region) and price:
                hist[region].append((month, price))
        out = []
        for region, lst in hist.items():
            lst.sort(key=lambda kv: kv[0])
            if len(lst) < 2 or not lst[-2][1]:
                continue
            mom = (lst[-1][1] / lst[-2][1] - 1) * 100
            out.append({
                "name": region,
                "month": lst[-1][0],
                "price": round(lst[-1][1], 2),
                "mom": round(mom, 2),
            })
        out.sort(key=lambda x: -x["price"])
        return out

    def region_blocks(self, region: str) -> dict:
        """某区域下各板块的截面行情：在挂套数、平均总价、相对本区均价的偏离。

        口径说明：house_price_trend 只有区域级月度序列，没有板块级历史，
        所以这里给的是「截面结构差异」（板块均价 vs 本区均价），不做时间环比。
        """
        region = (region or "").strip()
        if not region:
            return {"status": "error", "error": "缺少区域参数"}

        now = time.time()
        hit = _block_cache.get(region)
        if hit and (now - hit["ts"]) < _CACHE_TTL:
            return hit["data"]

        rows = self.db.query(
            HouseInfo.block, HouseInfo.address, HouseInfo.price, HouseInfo.area
        ).filter(HouseInfo.region == region).all()

        block_price = defaultdict(list)
        block_unit = defaultdict(list)
        block_count = Counter()
        comm_price = defaultdict(list)
        comm_unit = defaultdict(list)
        comm_count = Counter()
        all_prices = []

        for block, address, price_str, area_str in rows:
            p = self._price(price_str)
            a = self._area(area_str)
            if p:
                all_prices.append(p)

            # 板块：为空、或被误写成区县名（脏数据）时跳过
            bname = (block or "").strip()
            if bname and not is_district(bname) and bname != region:
                block_count[bname] += 1
                if p:
                    block_price[bname].append(p)
                if p and a:
                    block_unit[bname].append(p / a)

            # 小区：block 字段覆盖不足时用真实小区名兜底
            cname = (address or "").strip()
            if cname and cname != region:
                comm_count[cname] += 1
                if p:
                    comm_price[cname].append(p)
                if p and a:
                    comm_unit[cname].append(p / a)

        region_avg = sum(all_prices) / len(all_prices) if all_prices else 0

        def _build(counts, prices_map, units_map, min_count, top_n):
            out = []
            for name, cnt in counts.items():
                prices = prices_map.get(name) or []
                if cnt < min_count or not prices:
                    continue
                avg = sum(prices) / len(prices)
                units = units_map.get(name) or []
                out.append({
                    "name": name,
                    "count": cnt,
                    "avg_price": round(avg, 2),
                    "avg_unit_price": round(sum(units) / len(units), 2) if units else None,
                    "vs_region_pct": round((avg - region_avg) / region_avg * 100, 1) if region_avg else 0,
                })
            out.sort(key=lambda x: -x["count"])
            out = out[:top_n]
            out.sort(key=lambda x: -x["avg_price"])
            return out

        blocks = _build(block_count, block_price, block_unit, 3, 12)
        communities = _build(comm_count, comm_price, comm_unit, 5, 12)

        data = {
            "status": "ok",
            "region": region,
            "region_avg_price": round(region_avg, 2),
            "block_total": len(blocks),
            "community_total": len(communities),
            "blocks": blocks,
            "communities": communities,
        }
        _block_cache[region] = {"ts": now, "data": data}
        return data

    @staticmethod
    def _price_bucket(p: float) -> str:
        for hi, label in [(200, "<200万"), (400, "200-400万"), (700, "400-700万"),
                          (1000, "700-1000万"), (1500, "1000-1500万")]:
            if p < hi:
                return label
        return ">1500万"

    @staticmethod
    def _area_bucket(a: float) -> str:
        for hi, label in [(60, "<60㎡"), (90, "60-90㎡"), (120, "90-120㎡"),
                          (160, "120-160㎡"), (200, "160-200㎡")]:
            if a < hi:
                return label
        return ">200㎡"

    @staticmethod
    def _direction_group(d: str) -> str:
        d = d or "其他"
        if "南北" in d or "南" in d:
            return "朝南"
        if "东" in d:
            return "朝东"
        if "西" in d:
            return "朝西"
        if "北" in d:
            return "朝北"
        return "其他"
