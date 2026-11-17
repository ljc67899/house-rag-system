# -*- coding: utf-8 -*-
"""北京行政区县白名单。

house_info.region 里混有历史脏值（例如城市级 "北京"），它们既不是
行政区，也不该出现在筛选下拉框和"覆盖区域"统计口径里。凡是需要
按行政区行政区县口径输出的地方，统一走这里过滤。
"""

# 北京 16 个市辖区
BEIJING_DISTRICTS = (
    "东城区", "西城区", "朝阳区", "丰台区", "石景山区", "海淀区", "门头沟区",
    "房山区", "通州区", "顺义区", "昌平区", "大兴区", "怀柔区", "平谷区",
    "密云区", "延庆区",
)

_DISTRICT_SET = set(BEIJING_DISTRICTS)


def normalize_region(name: str) -> str:
    """去掉首尾空白，统一为裸字符串。"""
    return (name or "").strip()


def is_district(name: str) -> bool:
    """是否为合法行政区县名（白名单判断，不做模糊匹配）。"""
    return normalize_region(name) in _DISTRICT_SET
