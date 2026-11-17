# -*- coding: utf-8 -*-
"""房源结构化条件的判定（线上检索与离线评测共用同一份）

为什么单独抽成一个模块：
    早先这套判定写在 `RAGGraph._constraint_eval` 里，离线评测又照着重写了一个
    「按 page_content 做字符串包含」的 `_sat_ratio`。两套口径一旦分叉，
    就会出现「评测说答对了、线上说答错了」的假分数，而且极难发现。
    所以判定只留一份，线上和 `scripts/eval_rag.py` 都 import 这里。

硬约束 / 软约束为什么分开：
    region / price / area 是用户给的范围，不满足就是答非所问 → 下沉；
    rooms / 朝向 / 装修 / 电梯 是偏好，用户说「两居」但一套更便宜的三居也值得推
    → 只降权不排除。
"""

import re
from typing import Dict, List

from langchain_core.documents import Document

# 价格/面积容差：用户说「300 万以下」时，305 万的好房不该被一刀切掉
TOLERANCE = 0.05


def doc_fields(doc: Document) -> Dict[str, str]:
    """把 `字段：值；字段：值` 的房源正文解析成 dict

    朝向/装修/电梯没有写进向量元数据（元数据只有 region/address/rooms/price/area），
    所以这部分约束判定要回到正文取。候选只有几十条，字符串切分开销可忽略。
    """
    fields: Dict[str, str] = {}
    for part in (doc.page_content or "").split("；"):
        if "：" in part:
            name, value = part.split("：", 1)
            fields[name.strip()] = value.strip()
    return fields


def parse_num(value, default=None):
    """从 '449万' / '64平米' 这类字符串里取数字"""
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"(\d+(?:\.\d+)?)", str(value or ""))
    return float(match.group(1)) if match else default


def as_rooms_list(value) -> List[str]:
    """户型条件统一成列表：'2室' / ['2室', '3室'] 都接受

    支持多值是为了「两居或三居」这种说法——早先只取第一个数字，
    第二选项被直接丢掉。
    """
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def eval_constraints(doc: Document, filters: dict, relaxed: tuple = ()) -> dict:
    """评估一条候选对结构化条件的满足情况

    返回：
        hard_ok  硬约束是否全部通过
        soft     软约束满足比例（0~1）
        address  是否是用户点名的小区
        relaxed  实际跳过的维度集合（由调用方传入，原样带回便于排查）
    """
    filters = filters or {}
    meta = doc.metadata or {}
    fields = doc_fields(doc)
    skipped = set(relaxed or ())

    hard_ok = True

    # ---- 硬约束 ----
    if filters.get("region") and "region" not in skipped:
        if str(meta.get("region") or "") != str(filters["region"]):
            hard_ok = False

    if hard_ok and "price" not in skipped:
        price = parse_num(meta.get("price"))
        p_min, p_max = filters.get("price_min"), filters.get("price_max")
        if price is None:
            hard_ok = False
        else:
            if p_max and price > p_max * (1 + TOLERANCE):
                hard_ok = False
            if p_min and price < p_min * (1 - TOLERANCE):
                hard_ok = False

    if hard_ok and "area" not in skipped:
        area = parse_num(meta.get("area"))
        a_min, a_max = filters.get("area_min"), filters.get("area_max")
        if area is not None:
            if a_min and area < a_min * (1 - TOLERANCE):
                hard_ok = False
            if a_max and area > a_max * (1 + TOLERANCE):
                hard_ok = False

    # ---- 软约束 ----
    checks: List[bool] = []

    want_rooms = as_rooms_list(filters.get("rooms"))
    if want_rooms:
        have_rooms = str(meta.get("rooms") or "")
        checks.append(any(r in have_rooms for r in want_rooms))

    if filters.get("orientation"):
        want = str(filters["orientation"])
        have = fields.get("朝向", "")
        checks.append(("南" in have and "北" in have) if want == "南北" else (want in have))

    if filters.get("decoration"):
        blob = fields.get("房屋优势", "") + fields.get("房屋类型", "")
        checks.append(str(filters["decoration"]) in blob)

    if filters.get("elevator"):
        checks.append("电梯" in fields.get("配套设施", ""))

    soft = (sum(1 for ok in checks if ok) / len(checks)) if checks else 1.0

    return {
        "hard_ok": hard_ok,
        "soft": soft,
        "address": str(meta.get("address") or "") in (filters.get("address") or []),
    }


def satisfied(doc: Document, filters: dict, relaxed: tuple = ()) -> bool:
    """这条候选是否「答对了」：硬约束通过 且 软约束全中

    评测里的 strict@1 就是拿它判的——和线上约束分层用的是同一个函数，
    所以不会出现「线上认为不合格、评测认为合格」的分叉。
    """
    r = eval_constraints(doc, filters, relaxed)
    return bool(r["hard_ok"]) and float(r["soft"]) >= 1.0
