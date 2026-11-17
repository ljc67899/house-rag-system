# -*- coding: utf-8 -*-
"""价格解析工具：统一将房源价格字符串解析为「万元」数值"""

from typing import Optional


def monthly_payment(total_wan: float, down_payment_ratio: float = 0.3,
                    annual_rate: float = 0.0385, years: int = 30) -> Optional[float]:
    """等额本息月供（万元），用于对比页"买得起吗"的直观参考"""
    try:
        total = float(total_wan)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    loan = total * 10000 * (1 - down_payment_ratio)
    r = annual_rate / 12
    n = years * 12
    if r <= 0:
        return round(loan / n / 10000, 3)
    pay = loan * r * (1 + r) ** n / ((1 + r) ** n - 1)
    return round(pay / 10000, 3)


def parse_price_to_wan(price) -> Optional[float]:
    """把价格统一解析为以「万」为单位的数值。

    支持格式：\n
    - ``"1238万"`` -> 1238.0\n
    - ``"8500元/月"`` -> 0.85\n
    - ``"1238"`` -> 1238.0\n
    解析失败返回 None。
    """
    if price is None:
        return None
    price_str = str(price).replace(",", "").strip()
    if not price_str:
        return None
    try:
        if "万" in price_str:
            return float(price_str.replace("万", "").strip())
        if "元/月" in price_str:
            yuan_str = price_str.replace("元/月", "").strip()
            return float(yuan_str) / 10000.0
        if "元" in price_str:
            yuan_str = price_str.replace("元", "").strip()
            return float(yuan_str) / 10000.0
        return float(price_str)
    except (ValueError, TypeError):
        return None


def parse_area_to_sqm(area) -> Optional[float]:
    """把面积字符串解析为平方米数值。

    支持格式：``"141平米"`` / ``"79.5平方米"`` / ``"88㎡"`` / ``141``；解析失败返回 None。
    """
    if area is None:
        return None
    area_str = str(area).replace(",", "").strip()
    if not area_str:
        return None
    for unit in ("平方米", "平米", "平方", "㎡", "m²", "m2", "平"):
        area_str = area_str.replace(unit, "")
    try:
        value = float(area_str.strip())
        return value if value > 0 else None
    except (ValueError, TypeError):
        return None


def format_price_wan(price) -> str:
    """把万元数值格式化为展示用字符串（如 1238万 / 8500元/月）。"""
    if price is None:
        return "-"
    try:
        value = float(price)
    except (ValueError, TypeError):
        return str(price)
    if value < 1:
        return f"{int(round(value * 10000))}元/月"
    return f"{value:g}万"
