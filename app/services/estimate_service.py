# -*- coding: utf-8 -*-
"""房价智能估算服务

估算逻辑：区域真实单价均值 × 面积 × 特征系数。
返回三类信息，缺一不可：
    1. 估值本身：区间 + 均价 + 单价；
    2. **特征拆解**（factors_detail）：每项特征的百分比与折算金额（万元），
       两者满足 Σamount = 均价 - 区域基准价，可直接画成瀑布图/条形图；
    3. **市场定位**（market）：同区域真实单价分布与你所处的分位，
       数据不足时返回 None，前端显示"数据不足"而不是编一个排名。
"""

from sqlalchemy.orm import Session

from app.models.house import HouseInfo
from app.services.market_stats import MarketStatsService
from app.utils.price_utils import parse_area_to_sqm, parse_price_to_wan


class EstimateService:
    def __init__(self, db: Session):
        self.db = db
        self.market = MarketStatsService(db)

    # ---------- 特征系数 ----------

    @staticmethod
    def _build_factors(area: float, rooms: str, direction: str,
                       decoration: str, floor: str):
        """返回 [(key, label, multiplier, note)] 与兼容旧前端的 factors 字典"""
        seq = []

        # 面积：小户型单价更高，大户型有折价
        if area < 40:
            seq.append(("面积", "面积", 1.15, "小户型，单价上浮"))
        elif area > 120:
            seq.append(("面积", "面积", 0.90, "大面积，单价折让"))
        else:
            seq.append(("面积", "面积", 1.00, "中等面积，按基准价"))

        # 装修
        deco_map = {"毛坯": 0.85, "简装": 0.95, "中装": 1.0, "精装修": 1.15, "精装": 1.15}
        deco_score = deco_map.get(decoration, 1.0)
        seq.append(("装修", "装修", deco_score, decoration))

        # 朝向
        if "南" in direction:
            seq.append(("朝向", "朝向", 1.08, f"{direction}向通透"))
        elif "东" in direction:
            seq.append(("朝向", "朝向", 1.05, "东向"))
        elif "西" in direction:
            seq.append(("朝向", "朝向", 0.92, "西向，采光偏强但西晒"))
        else:
            seq.append(("朝向", "朝向", 1.00, f"{direction}向"))

        # 楼层
        floor_map = {"低楼层": 0.95, "中楼层": 1.0, "高楼层": 1.05, "顶层": 0.98}
        floor_score = 1.0
        for key, value in floor_map.items():
            if key in floor:
                floor_score = value
                break
        seq.append(("楼层", "楼层", floor_score, floor))

        # 户型
        if "1室" in rooms:
            seq.append(("户型", "户型", 1.10, "一居室稀缺"))
        elif "4室" in rooms or "5室" in rooms:
            seq.append(("户型", "户型", 0.95, "多居室，总价高、单价折让"))
        else:
            seq.append(("户型", "户型", 1.00, rooms))

        old_style = {}
        for key, label, mult, note in seq:
            if abs(mult - 1) < 1e-9:
                old_style[label] = f"{note} 标准价"
            else:
                sign = "+" if mult > 1 else ""
                old_style[label] = f"{note} {sign}{round((mult - 1) * 100)}%"
        return seq, old_style

    # ---------- 估算 ----------

    def estimate_price(self, rooms: str = "2室1厅", area: float = 80,
                       region: str = "朝阳区", direction: str = "南北",
                       decoration: str = "精装修", floor: str = "中层"):
        """基于同区域真实挂牌数据 + 房源特征系数进行房价估算"""
        try:
            area = float(area)
        except (TypeError, ValueError):
            area = 80.0
        if area <= 0:
            area = 80.0

        # 1. 收集同区域房源作为基准（优先精确匹配，命中索引）
        houses = self.db.query(HouseInfo).filter(HouseInfo.region == region).all()
        if not houses:
            houses = self.db.query(HouseInfo).filter(
                HouseInfo.region.like(f"%{region}%")
            ).limit(100).all()

        price_area_pairs = []
        for h in houses:
            price_val = parse_price_to_wan(h.price)
            area_val = parse_area_to_sqm(h.area)
            if price_val and price_val > 0 and area_val and area_val > 0:
                price_area_pairs.append((price_val, area_val, h))

        # 2. 走势参考（LSTM / 统计外推），任何分支都能返回
        lstm_reference = None
        lstm_method = None
        try:
            from app.services.trend_service import TrendService
            trend = TrendService(self.db).get_trend(region, predict_months=3)
            if trend.get("status") == "ok" and trend.get("predictions"):
                lstm_reference = round(trend["predictions"][-1]["price"])
                lstm_method = trend.get("method", "LSTM 深度学习时序预测")
        except Exception as e:
            import logging
            logging.warning(f"LSTM reference failed: {e}")

        region_stats = self.market.get_region_stats(region)

        # 3. 数据不足：给保守估算并如实标注
        if not price_area_pairs:
            base_per_sqm = 7.5
            estimated = base_per_sqm * area
            return {
                "estimated_price_min": round(estimated * 0.85),
                "estimated_price_max": round(estimated * 1.15),
                "estimated_price_avg": round(estimated),
                "unit_price": round(base_per_sqm, 2),
                "base_unit_price": base_per_sqm,
                "confidence": 0.3,
                "confidence_basis": "同区域无有效挂牌样本，采用默认基准单价",
                "factors": {"区域": region, "面积": area, "户型": rooms},
                "factors_detail": [],
                "similar_houses_count": 0,
                "market": MarketStatsService.public(region_stats) | {"your_unit_price": round(base_per_sqm, 2), "percentile": None},
                "advice": "该区域有效挂牌样本不足，以下结果按全市默认单价估算，仅作占位参考",
                "lstm_reference_price": lstm_reference,
                "lstm_method": lstm_method,
                "sample_basis": f"同区域可解析样本 {len(price_area_pairs)} 套",
            }

        # 4. 区域真实单价基准
        per_sqms = [p / a for p, a, _ in price_area_pairs]
        avg_per_sqm = sum(per_sqms) / len(per_sqms)

        # 5. 特征拆解：按顺序相乘，同时记录每一步的增量金额
        seq, old_style_factors = self._build_factors(area, rooms, direction, decoration, floor)
        running = avg_per_sqm * area
        details = []
        for key, label, mult, note in seq:
            delta = running * (mult - 1)
            running += delta
            details.append({
                "key": key,
                "label": label,
                "pct": round((mult - 1) * 100),
                "amount": round(delta, 1),
                "note": note,
            })
        base_estimate = running
        unit_price = base_estimate / area

        min_estimate = base_estimate * 0.88
        max_estimate = base_estimate * 1.12

        # 可信度：由「区域单价离散度 + 样本量」推导，而不是"样本一多就 95%"
        # 离散度越大，说明同区域房子差异越大，单一数字越不可靠
        try:
            from statistics import stdev
            dispersion = stdev(per_sqms) / avg_per_sqm if len(per_sqms) > 1 and avg_per_sqm else 0.0
        except Exception:
            dispersion = 0.0
        sample_score = min(1.0, len(price_area_pairs) / 300.0)
        confidence = 0.95 - min(0.35, dispersion * 0.8) - (1 - sample_score) * 0.15
        confidence = max(0.4, min(0.92, confidence))
        percentile = self.market.percentile_of(region, unit_price)

        # 6. 建议文案：只陈述算出来的事实
        advice_parts = [
            f"参考 {len(price_area_pairs)} 套{region}在挂房源，区域平均单价 {round(avg_per_sqm, 2)} 万/㎡",
        ]
        if percentile is not None:
            advice_parts.append(f"按此单价，你在本区房源中处于第 {percentile} 百分位")
        if confidence > 0.8:
            advice_parts.append("样本充足，可作为挂牌/还价的基准。")
        elif confidence > 0.6:
            advice_parts.append("样本一般，建议结合具体房源楼层朝向再看一眼。")
        else:
            advice_parts.append("样本偏少，仅供初步参考。")

        return {
            "estimated_price_min": round(min_estimate),
            "estimated_price_max": round(max_estimate),
            "estimated_price_avg": round(base_estimate),
            "unit_price": round(unit_price, 2),
            "base_unit_price": round(avg_per_sqm, 2),
            "confidence": round(confidence, 2),
            "confidence_basis": (
                f"由 {len(price_area_pairs)} 套样本与区域单价离散度 {dispersion:.1%} 推导，"
                f"离散度越高说明同区域房源差异越大、单一估值越不可靠"
            ),
            "factors": old_style_factors,
            "factors_detail": details,
            "similar_houses_count": len(price_area_pairs),
            "market": MarketStatsService.public(region_stats) | {
                "your_unit_price": round(unit_price, 2),
                "percentile": percentile,
            },
            "advice": "；".join(advice_parts),
            "lstm_reference_price": lstm_reference,
            "lstm_method": lstm_method,
            "sample_basis": f"同区域可解析样本 {len(price_area_pairs)} 套",
        }
