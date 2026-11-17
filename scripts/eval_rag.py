#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAG 检索评测：检索排序 + 条件抽取 + Supervisor 路由

为什么要拆成两套评测：
    v3 用自然语言出题、让线上正则去抽条件，一次跑分同时测了「抽得准」和「排得准」。
    mixed 层 strict@1 掉到 0.850 时，花了半天才发现真因是正则漏了口语的「两」
    （「南十里居 两居」压根没抽出户型），排序逻辑其实没问题。
    两件事混测，分数掉下来定位不到是哪一环坏的。

    所以现在：
        retrieval  —— 题目直接给 filters，只考排序
        extraction —— 给自然语言，只考条件抽取
    strict@1 的判定调 `app.services.house_filters.satisfied`，与线上约束分层同一份代码，
    不会出现「线上说答错了、评测说答对了」的分叉。

五档检索对比：
    dense            = 只有稠密向量
    sparse           = 只有 BM25
    hybrid_nofilter  = 混合召回但 filters=None，退化为纯相关性排序
    hybrid_no_div    = 约束排序但关掉小区级去重
    hybrid           = 线上路径（约束分层 + 小区级去重）
    hybrid 与 hybrid_nofilter 在 mixed 层的差值，就是「约束排序」本身的贡献。

指标为什么要重做（v6 的教训）：
    v6 全套指标都是 1.000。逐条查下来不是模型强，是尺子坏了：
      1. MRR 按「小区名对不对」算名次，而约束分层本来就把点名小区排在最前 → 恒为 1.000；
      2. 题目都出在合格集极小、第一名必然命中的小区上，没有区分空间；
      3. 门禁写的是「hybrid >= sparse」，两边都满分 → 恒为真；
      4. 放宽（relaxed）不进任何指标，「宣布放宽、第一名却合格」照样满分。
    现在改成：名次按「第一条真正合格」算、每题先算库内合格集规模并据此归一覆盖率、
    单独统计假放宽、再加一张召回覆盖诊断表（区分「没捞到」与「排到 k 之外」），
    门禁用绝对阈值。换更难的模型之前，先把尺子修准，否则改什么都看不出差别。

用法（项目根目录）：
    python scripts/eval_rag.py
    python scripts/eval_rag.py --top-k 5 --out docs/eval_report.md
    python scripts/eval_rag.py --routing            # 额外评 Supervisor 路由（会调 LLM）
    python scripts/eval_rag.py --no-extraction      # 跳过条件抽取评测
"""

import argparse
import asyncio
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.documents import Document  # noqa: E402

from app.services.house_filters import eval_constraints, satisfied  # noqa: E402
from app.services.langchain_adapters import get_house_store  # noqa: E402
from app.services.rag_graph import RAGGraph, ROUTE_HYBRID  # noqa: E402
from app.services.sparse_index import doc_key as _house_doc_key, get_house_bm25  # noqa: E402
from app.services.vector_store import get_vector_store  # noqa: E402

SET_PATH = ROOT / "data" / "eval" / "retrieval_set.json"
EXTRACT_PATH = ROOT / "data" / "eval" / "extraction_set.json"


# ---------- 五档检索 ----------
#
# 统一返回 [{"doc": Document, "relaxed": [放宽的维度]}]，而不是裸 Document：
# 只有 hybrid 路径会带 relaxed，但「有没有放宽、放宽了几个维度」是判分的一部分——
# 以前把它丢了，于是「宣布放宽了价格，第一条却在预算内」这种自相矛盾的输出
# 在分数上完全看不出来。

def _plain(docs):
    return [{"doc": d, "relaxed": []} for d in docs]


def dense_hits(query: str, filters: dict, k: int):
    pairs = get_house_store().similarity_search_with_score(query, k=k, score_threshold=None)
    return _plain([doc for doc, _ in pairs])


def sparse_hits(query: str, filters: dict, k: int):
    bm25 = get_house_bm25()
    if not bm25:
        return []
    docs = []
    for key, _score in bm25.search(query, k=k):
        content, meta = bm25.raw.get(key, ("", {}))
        if content:
            docs.append(Document(page_content=content, metadata=meta))
    return _plain(docs)


def hybrid_hits(graph: RAGGraph, query: str, filters: dict, k: int):
    """线上路径：filters 直接参与约束分层排序"""
    return graph._house_hybrid_recall(query, filters=filters)[:k]


def hybrid_hits_nofilter(graph: RAGGraph, query: str, filters: dict, k: int):
    """对照档：filters=None → 退化为纯相关性排序，用来看约束排序值多少钱"""
    return graph._house_hybrid_recall(query)[:k]


def hybrid_hits_no_div(graph: RAGGraph, query: str, filters: dict, k: int):
    """关掉小区级去重的对照档：用来证明 precision 下降是多样性换来的，不是退化"""
    old = graph.diversity
    graph.diversity = False
    try:
        return graph._house_hybrid_recall(query, filters=filters)[:k]
    finally:
        graph.diversity = old


# ---------- 库内合格集（题目的难度底座） ----------
#
# 为什么要全库统计：判「有没有答对」必须知道「能答对的到底有几套」。
# 合格集只有 1 套的题（M021/M022）和 23 套的题（M001），同样 top1 正确，
# 但难度、覆盖上限、以及「该不该放宽」的答案完全不同。
# 早先没有这个底座，于是出现「所有指标都是 1.000」——因为题目恰好都出在
# 合格集极小、且第一名必然命中的小区上，指标根本没有区分空间。

def load_library():
    docs = []
    for _key, (content, meta) in get_vector_store().get_all_documents("house").items():
        docs.append(Document(page_content=content, metadata=meta or {}))
    return docs


_ELIGIBLE_CACHE = {}


def eligible_keys(items, library) -> dict:
    """每题在**全库**里的合格集（key 集合），按 filters 缓存

    注意：`satisfied()` 只判硬/软约束，**不看小区名**（线上口径如此：小区的对错由
    约束分层单独处理）。所以这里要自己补上「点名了小区就得是那个小区」——
    否则「公园大道 双龙南里 两居 ≤400 万」的合格集会算成全库 1529 套七百万内的两居，
    覆盖率分母被放大三个数量级，指标当场失去意义。
    """
    out = {}
    for it in items:
        filters = it.get("filters") or {}
        cache_key = json.dumps(filters, ensure_ascii=False, sort_keys=True)
        if cache_key not in _ELIGIBLE_CACHE:
            expect = set(filters.get("address") or [])
            keys = set()
            for doc in library:
                meta = doc.metadata or {}
                if expect and meta.get("address") not in expect:
                    continue
                if satisfied(doc, filters):
                    k_ = _house_doc_key(meta)
                    if k_:
                        keys.add(k_)
            _ELIGIBLE_CACHE[cache_key] = keys
        out[it["id"]] = _ELIGIBLE_CACHE[cache_key]
    return out


# ---------- 检索指标 ----------

def _eval_item(it, hits, k: int, eligible=frozenset()) -> dict:
    """单题打分：只算原始事实，聚合交给 _agg_tier

    filters 来自题目本身（不是从问题里现抽），所以这一层测的纯粹是排序。
    判定统一走 house_filters，与线上同一份代码。

    两套名次（这是「MRR 恒为 1.000」的根因）：
        rank_addr —— 第一条「小区名对」的名次。约束分层把点名小区的候选全排到最前，
                     所以这半个维度永远满分，MRR 也就永远 ≈ 1.000。
        rank      —— 第一条「真正合格」的名次（地址对 + 硬约束 + 软约束，judged by satisfied）。
                     这才是用户要的东西，MRR/nDCG 现在按它算。
    """
    hits = hits[:k]
    docs = [h["doc"] for h in hits]
    relaxed = list((hits[0].get("relaxed") if hits else None) or [])
    filters = it.get("filters") or {}
    expect = set(filters.get("address") or [])
    eligible_n = len(eligible)

    def ok(doc):
        """真正合格 = 约束全中 + （点名了小区就）小区对上

        `satisfied()` 只判约束、不看小区名（线上口径如此），所以这里必须自己补上，
        否则「公园大道的两居」会被一条「双龙南里的两居」判成合格，
        MRR 又会回到「随便一条就算对」的老路。
        """
        if not satisfied(doc, filters):
            return False
        return (not expect) or (doc.metadata or {}).get("address") in expect

    rank = next((i for i, d in enumerate(docs, 1) if ok(d)), None)
    rank_addr = None
    if expect:
        rank_addr = next(
            (i for i, d in enumerate(docs, 1) if (d.metadata or {}).get("address") in expect),
            None,
        )

    top = docs[0] if docs else None
    top_addr = bool(expect) and bool(top) and (top.metadata or {}).get("address") in expect
    top_ok = bool(top) and satisfied(top, filters)
    top_strict = bool(top) and ok(top)
    softs = [float(eval_constraints(d, filters)["soft"]) for d in docs]
    oks = [satisfied(d, filters) for d in docs]
    n_ok = sum(1 for d in docs if ok(d))

    matched = sum(1 for d in docs if (d.metadata or {}).get("address") in expect) if expect else 0
    addrs = {(d.metadata or {}).get("address") for d in docs}
    addrs.discard(None)

    # 覆盖率：top-k 里拿到的合格条数 / 「本来最多能拿到几条」
    #     分母 min(k, 库内合格集) —— 合格集有 23 套时上限就是 k，能拿满才算满；
    #     合格集只有 1 套时，拿到那 1 套即满分，不会因为它没凑够 5 条被扣分。
    #     没有这层归一，「大合格集」的题和「唯一一套」的题在指标上没法比。
    cov = None
    if filters and eligible_n > 0:
        cov = n_ok / min(k, eligible_n)

    return {
        "rank": rank,
        "rank_addr": rank_addr,
        "top_addr": top_addr,
        "top_ok": top_ok,
        "top_strict": top_strict,
        "top_soft": softs[0] if softs else 0.0,
        "best_ok": any(oks),
        "best_soft": max(softs, default=0.0),
        "precision": (matched / len(docs)) if docs else 0.0,
        # top-k 里真正合格的占比。semantic 层没有小区名可对，只能靠这个——
        # 只判 top1 太宽松（随机一条两居就算对），precision@k 才看得出推荐质量
        "precision_ok": (n_ok / len(docs)) if docs else 0.0,
        "cov": cov,
        "eligible_n": eligible_n,
        "relaxed": relaxed,
        "distinct": (len(addrs) / len(docs)) if docs else 0.0,
        "got": [(d.metadata or {}).get("address") for d in docs],
    }


def _agg_tier(pairs, k: int) -> dict:
    n = len(pairs) or 1
    res = {"n": len(pairs)}

    with_addr = [(it, s) for it, s in pairs if (it.get("filters") or {}).get("address")]
    if with_addr:
        m = len(with_addr)
        res["recall"] = sum(1 for _it, s in with_addr if s["rank"]) / m
        res["recall1"] = sum(1 for _it, s in with_addr if s["rank"] == 1) / m
        res["mrr"] = sum((1.0 / s["rank"]) if s["rank"] else 0.0 for _it, s in with_addr) / m
        res["ndcg"] = sum(
            (1.0 / math.log2(s["rank"] + 1)) if s["rank"] else 0.0 for _it, s in with_addr
        ) / m
        res["precision"] = sum(s["precision"] for _it, s in with_addr) / m
        res["strict1"] = sum(1 for _it, s in with_addr if s["top_addr"] and s["top_ok"]) / m
        res["strict_n"] = m

    with_cons = [(it, s) for it, s in pairs if _has_soft(it)]
    if with_cons:
        m = len(with_cons)
        res["sat1"] = sum(1 for _it, s in with_cons if s["top_ok"]) / m
        res["sat_at_k"] = sum(1 for _it, s in with_cons if s["best_ok"]) / m
        res["precision_ok"] = sum(s["precision_ok"] for _it, s in with_cons) / m

    # 「判得了对错」的题：给了条件（不是空 filters）且库里真有合格房。
    # 空 filters 的语义题（「采光好的高层」）没有对错标准，混进分母只会把分数洗白。
    judged = [(it, s) for it, s in pairs if s["cov"] is not None]
    if judged:
        m = len(judged)
        res["cov"] = sum(s["cov"] for _it, s in judged) / m
        res["precision_ok_all"] = sum(s["precision_ok"] for _it, s in judged) / m
        res["ok1_all"] = sum(1 for _it, s in judged if s["top_strict"]) / m
        # 零覆盖：top-k 里一条合格的都没有。均值会被简单题稀释（17 题里有 2 题全错，
        # 均值仍能到 0.8），所以单独数题数——这类题是「这条路径根本没答上来」。
        res["zero_cov"] = sum(1 for _it, s in judged if not s["cov"])
        # 假放宽：库里明明有合格房，检索却宣布放宽了维度。
        # 这是之前那套门禁恒真的地方——旧口径下「放宽」不进任何指标，
        # 于是「宣布放宽、但第一名其实合格」这种自相矛盾的输出照样满分。
        res["relax_bad"] = sum(1 for _it, s in judged if s["relaxed"]) / m
        res["judged_n"] = m
        sizes = [s["eligible_n"] for _it, s in judged]
        res["eligible_med"] = statistics.median(sizes)
        res["eligible_min"] = min(sizes)
        res["eligible_max"] = max(sizes)

    res["distinct"] = sum(s["distinct"] for _it, s in pairs) / n
    return res


def _has_soft(it) -> bool:
    f = it.get("filters") or {}
    return any(f.get(k) for k in ("rooms", "orientation", "decoration", "elevator"))


def eval_retrieval(items, hit_fn, k: int, eligible_map=None):
    """按 literal / mixed / semantic 三层分别聚合

    为什么要分层而不是一个大平均：
        literal 题 BM25 天生占优，mixed 题才是「结构化约束有没有真正参与排序」的
        探针。混在一起算平均，literal 的高分会把 mixed 的失败掩盖掉。
    """
    # conflict 层的对错由 eval_conflict 单独判（放宽与否），不掺进排序分数
    items = [it for it in items if it.get("type") != "conflict"]
    eligible_map = eligible_map or {}

    per_item = []
    for it in items:
        hits = hit_fn(it["question"], it.get("filters") or {})
        per_item.append((it, _eval_item(it, hits, k, eligible_map.get(it["id"]) or frozenset())))

    tiers = {}
    for tier in ("literal", "mixed", "semantic"):
        tiers[tier] = _agg_tier([p for p in per_item if p[0].get("type") == tier], k)
    tiers["all"] = _agg_tier(per_item, k)

    fails = [
        {
            "id": it["id"],
            "type": it.get("type"),
            "question": it["question"],
            "filters": it.get("filters") or {},
            "got": s["got"],
            "top_soft": round(s["top_soft"], 2),
        }
        for it, s in per_item
        if (it.get("filters") or {}).get("address") and not (s["top_addr"] and s["top_ok"])
    ]

    # 合格集非空却没答出合格结果的题：按「召回腿没捞到」/「捞到了但排到 k 之外」分类，
    # 因为这两种失败要改的代码完全不同（改召回容量 vs 改排序）。
    misses = [
        {
            "id": it["id"],
            "type": it.get("type"),
            "question": it["question"],
            "eligible_n": s["eligible_n"],
            "rank": s["rank"],
            "relaxed": s["relaxed"],
            "got": s["got"],
        }
        for it, s in per_item
        if s["eligible_n"] > 0 and s["rank"] is None
    ]
    relax_bads = [
        {
            "id": it["id"],
            "type": it.get("type"),
            "question": it["question"],
            "eligible_n": s["eligible_n"],
            "relaxed": s["relaxed"],
            "top_ok": s["top_ok"],
        }
        for it, s in per_item
        if s["cov"] is not None and s["relaxed"]
    ]

    return {"tiers": tiers, "fails": fails, "misses": misses, "relax_bads": relax_bads}


def eval_recall_legs(graph: RAGGraph, items, eligible_map, k: int):
    """召回覆盖诊断：合格房到底有没有被「召回腿」捞进候选池

    这是把「答不出来」拆成两半的尺子：
        召回腿命中率低 → 候选池里根本没有对的房，再怎么排序也没用（改容量/改准入）；
        召回腿命中率高、top-k 命中低 → 排到 k 之外了（改排序）。
    早先只有 top-k 上的单一分数，看到 strict@1 掉分根本判断不了该动哪一段。
    """
    rows = []
    for it in items:
        if it.get("type") == "conflict":
            continue
        filters = it.get("filters") or {}
        eligible = eligible_map.get(it["id"]) or frozenset()
        if not filters or not eligible:
            continue
        q = it["question"]

        dense_leg = []
        try:
            dense_leg = get_house_store().similarity_search_with_score(
                q, k=graph.dense_recall_k, score_threshold=graph.score_threshold
            )
        except Exception:
            pass
        dense_hit = any(satisfied(d, filters) for d, _ in dense_leg)

        sparse_hit = False
        bm25 = get_house_bm25()
        if bm25:
            for key, _score in bm25.search(q, k=graph.sparse_recall_k):
                content, meta = bm25.raw.get(key, ("", ""))
                if content and satisfied(Document(page_content=content, metadata=meta or {}), filters):
                    sparse_hit = True
                    break

        rows.append({
            "id": it["id"],
            "type": it.get("type"),
            "dense": dense_hit,
            "sparse": sparse_hit,
            "either": dense_hit or sparse_hit,
        })

    tiers = {}
    for tier in ("literal", "mixed", "semantic", "all"):
        sel = rows if tier == "all" else [r for r in rows if r["type"] == tier]
        if not sel:
            tiers[tier] = {"n": 0}
            continue
        m = len(sel)
        tiers[tier] = {
            "n": m,
            "dense": sum(1 for r in sel if r["dense"]) / m,
            "sparse": sum(1 for r in sel if r["sparse"]) / m,
            "either": sum(1 for r in sel if r["either"]) / m,
        }
    return {"tiers": tiers, "rows": rows}


# ---------- 约束冲突评测 ----------

def eval_conflict(graph: RAGGraph, items, k: int):
    """条件互相打架时，有没有触发「放宽并如实说明」

    这是唯——类「正确答案是承认答不出来」的题，以前整个评测集里都没有。
    带来的问题很实在：条件根本满足不了时，系统硬凑五条交差，分数一点不掉。

    判据是检索返回的 `relaxed`：它记录了这一轮放宽了哪几个维度（area/price/region）。
    真正该放宽的题必须放开，本来能满足的题不该乱放——两个方向都要考，
    否则只要「无脑放宽」就能拿满分。
    """
    ok = 0
    fails = []

    for it in items:
        filters = it.get("filters") or {}
        hits = graph._house_hybrid_recall(it["question"], filters=filters)[:k]
        relaxed = list((hits[0].get("relaxed") if hits else None) or [])
        expect = bool(it.get("expect_relaxed"))

        if bool(relaxed) == expect:
            ok += 1
        else:
            fails.append({
                "id": it["id"],
                "question": it["question"],
                "expect_relaxed": expect,
                "got_relaxed": relaxed,
                "got": [(h["doc"].metadata or {}).get("address") for h in hits],
            })

    total = len(items) or 1
    return {
        "total": len(items),
        "ok": ok,
        "acc": ok / total,
        "should_relax": sum(1 for it in items if it.get("expect_relaxed")),
        "fails": fails,
    }


# ---------- 条件抽取评测 ----------

def _same(actual, want) -> bool:
    if isinstance(want, (list, tuple)):
        got = actual if isinstance(actual, (list, tuple)) else ([actual] if actual else [])
        return {str(g) for g in got} == {str(w) for w in want}
    if isinstance(want, bool):
        return bool(actual) == want
    if isinstance(want, (int, float)):
        try:
            return abs(float(actual) - float(want)) < 1e-6
        except (TypeError, ValueError):
            return False
    return str(actual) == str(want)


_EMPTY = (None, False, "", [], 0)


def eval_extraction(graph: RAGGraph, items):
    """字段级准确率 + 整题全对率

    只测 `_regex_filters`（纯正则、零成本那一层）。模型兜底是按需触发的，
    放在这里测会把「有时调模型有时不调」的随机性带进分数。
    """
    field_total = field_ok = 0
    item_ok = 0
    fails = []

    for it in items:
        got = graph._regex_filters(it["question"])
        bad = []

        for key, want in (it.get("expect") or {}).items():
            field_total += 1
            actual = got.get(key)
            if _same(actual, want):
                field_ok += 1
            else:
                bad.append(f"{key} 期望={want} 实得={actual}")

        for key in it.get("forbid") or []:
            field_total += 1
            actual = got.get(key)
            if actual in _EMPTY:
                field_ok += 1
            else:
                bad.append(f"{key} 期望为空 实得={actual}")

        if bad:
            fails.append({"id": it["id"], "question": it["question"], "bad": bad})
        else:
            item_ok += 1

    total = len(items) or 1
    return {
        "total": len(items),
        "item_ok": item_ok,
        "item_acc": item_ok / total,
        "field_total": field_total,
        "field_ok": field_ok,
        "field_acc": (field_ok / field_total) if field_total else 0.0,
        "fails": fails,
    }


# ---------- 路由评测 ----------

async def eval_routing(graph: RAGGraph, items):
    correct, total = 0, 0
    confusion = Counter()
    wrongs = []

    for it in items:
        route, reason = graph._rule_route(it["question"])
        if route == ROUTE_HYBRID:
            llm_route = await graph._llm_route(it["question"])
            if llm_route:
                route, reason = llm_route
        expect = it["expect_route"]
        # 允许标多个可接受答案：「望京那边怎么样」判 hybrid 或 analytics 都说得通，
        # 硬标一个会让稳定的题变成随机题（同一句问两次结果都不一样）
        accept = expect if isinstance(expect, (list, tuple)) else [expect]
        total += 1
        hit = route in accept
        confusion[("|".join(accept), route, hit)] += 1
        if hit:
            correct += 1
        else:
            wrongs.append({
                "id": it["id"], "question": it["question"],
                "expect": expect, "got": route, "reason": reason,
            })

    return {
        "accuracy": (correct / total) if total else 0.0,
        "total": total,
        "correct": correct,
        "confusion": confusion,
        "wrongs": wrongs,
    }


# ---------- 报告 ----------

def _fmt(v):
    return "-" if v is None else f"{v:.3f}"


def check_gates(results, legs):
    """发布门禁：绝对阈值，不再拿两档互相比较

    旧门禁是「hybrid mixed strict@1 >= sparse mixed strict@1」——两边都是 1.000，
    恒为真，等于没有门禁。判据得落在「绝对该达到多少」上，否则改坏了也照样打勾。

    阈值怎么定的（不按实测回调，按「用户能接受的最差情况」定）：
        mixed strict@1 >= 0.85   —— 22 题最多错 3 题。点名小区 + 户型/价格的题
                                    错一题就是答非所问，不能更多。
        semantic 覆盖@5 >= 0.80  —— 无小区名时全靠约束，top-5 里该拿到八成合格房。
        假放宽 == 0              —— 库里有合格房却说「已放宽条件」，等于给自己找台阶，
                                    这种自相矛盾的输出一条都不该有。
        零覆盖题 == 0            —— 均值会被简单题稀释（2 题全错仍能到 0.82），
                                    所以这类「根本没答上来」的题按条数判。
    """
    hy = (results.get("hybrid") or {}).get("tiers") or {}
    mixed = hy.get("mixed") or {}
    sem = hy.get("semantic") or {}
    allt = hy.get("all") or {}
    sparse_mixed = ((results.get("sparse") or {}).get("tiers") or {}).get("mixed") or {}

    checks = [
        {
            "name": f"mixed strict@1",
            "want": ">= 0.850",
            "got": mixed.get("strict1"),
            "ok": (mixed.get("strict1") or 0.0) >= 0.85,
            "why": "点名小区 + 户型/价格同时答对的比例",
        },
        {
            "name": f"semantic 覆盖@5",
            "want": ">= 0.800",
            "got": sem.get("cov"),
            "ok": (sem.get("cov") or 0.0) >= 0.80,
            "why": "无小区名时，合格房在 top-5 里的覆盖比例",
        },
        {
            "name": "假放宽（全部层级）",
            "want": "== 0.000",
            "got": allt.get("relax_bad"),
            "ok": (allt.get("relax_bad") or 0.0) == 0.0,
            "why": "库里有合格房却宣布放宽条件的比例",
        },
        {
            "name": "零覆盖题数（全部层级）",
            "want": "== 0",
            "got": float(allt.get("zero_cov") or 0),
            "ok": (allt.get("zero_cov") or 0) == 0,
            "why": "库里有合格房，top-k 里却一条合格的都没拿到",
        },
        {
            "name": "参考：hybrid vs sparse（mixed strict@1）",
            "want": ">= sparse",
            "got": mixed.get("strict1"),
            "ok": (mixed.get("strict1") or 0.0) + 1e-9 >= (sparse_mixed.get("strict1") or 0.0),
            "why": "只作参照，不再作为唯一依据（旧门禁恒真就死在这里）",
        },
    ]
    return checks


def build_report(results, conflict, extraction, routing, legs, gates, k, counts):
    lines = []
    lines.append("# RAG 检索评测报告\n")
    lines.append(f"- 检索集: `data/eval/retrieval_set.json`（top_k = {k}）")
    lines.append("- 抽取集: `data/eval/extraction_set.json`")
    lines.append("- 判定函数: `app.services.house_filters.eval_constraints`（线上/评测同一份）\n")

    lines.append("## 指标口径（先读这段，否则数字和旧报告对不上）\n")
    lines.append("| 指标 | 定义 | 为什么这么定义 |")
    lines.append("|---|---|---|")
    lines.append(
        "| strict@1 | 第一名「命中点名小区 **且** 约束全中」 | 用户同时给了小区和户型/价格，缺一不可 |"
    )
    lines.append(
        f"| 覆盖@k | top-k 里的合格条数 / min(k, 库内合格集) | 合格集 23 套 vs 1 套的题，"
        "「拿满」的含义不同；不按合格集归一，两类题没法比 |"
    )
    lines.append(
        "| MRR / nDCG | 按「第一条**真正合格**」的名次算 | 旧口径按「小区名对不对」算，"
        "而约束分层把点名小区全排到前面，于是恒为 1.000 |"
    )
    lines.append(
        "| 合格精度@k | top-k 里合格条数 / k | 看推荐质量，不看第一名；"
        "只判 top1 时库里两居占 36%，随机都能过 |"
    )
    lines.append(
        "| 假放宽 | 库内合格集非空却返回了 relaxed | 「已放宽价格」和「第一条在预算内」"
        "同时出现，是自相矛盾的输出 |"
    )
    lines.append(
        "| 库内合格集 | 全库所有房源按 filters 现算的达标条数 | 题目的难度底座；"
        "以前没有它，题目恰好都出在合格集极小的小区上，指标没有区分空间 |"
    )

    lines.append(
        f"\n## literal 层（{counts.get('literal', 0)} 题，纯小区名：只考命中与名次）\n"
    )
    lines.append(f"| 模式 | 命中@{k} | 命中@1 | MRR | nDCG@{k} | 小区精度@{k} | 覆盖@{k} |")
    lines.append("|---|---|---|---|---|---|---|")
    for mode, r in results.items():
        t = r["tiers"]["literal"]
        lines.append(
            f"| {mode} | {_fmt(t.get('recall'))} | {_fmt(t.get('recall1'))} | "
            f"{_fmt(t.get('mrr'))} | {_fmt(t.get('ndcg'))} | {_fmt(t.get('precision'))} | "
            f"{_fmt(t.get('cov'))} |"
        )

    lines.append(
        f"\n## mixed 层（{counts.get('mixed', 0)} 题，小区名 + 户型/价格：专测约束排序）\n"
    )
    lines.append(
        f"| 模式 | 小区 recall@1 | 约束满足@1 | **strict@1** | 覆盖@{k} | 合格精度@{k} | 假放宽 | 零覆盖题 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for mode, r in results.items():
        t = r["tiers"]["mixed"]
        lines.append(
            f"| {mode} | {_fmt(t.get('recall1'))} | {_fmt(t.get('sat1'))} | "
            f"**{_fmt(t.get('strict1'))}** | {_fmt(t.get('cov'))} | "
            f"{_fmt(t.get('precision_ok_all'))} | {_fmt(t.get('relax_bad'))} | "
            f"{t.get('zero_cov', '-')} |"
        )

    lines.append(f"\n## semantic 层（{counts.get('semantic', 0)} 题，无小区名锚点）\n")
    lines.append(
        f"| 模式 | 约束满足@1 | 合格精度@{k} | **覆盖@{k}** | 零覆盖题 | distinct@{k} |"
    )
    lines.append("|---|---|---|---|---|---|")
    for mode, r in results.items():
        t = r["tiers"]["semantic"]
        lines.append(
            f"| {mode} | {_fmt(t.get('sat1'))} | {_fmt(t.get('precision_ok_all'))} | "
            f"**{_fmt(t.get('cov'))}** | {t.get('zero_cov', '-')} | {_fmt(t.get('distinct'))} |"
        )
    lines.append("")
    lines.append("- 合格精度@k：top-k 里「真正满足条件」的比例。semantic 层没有小区名可对，")
    lines.append("  只判第一名的户型太宽松（库里两居占大头，随机一条都能过），看这个才有区分度。")
    lines.append("- 覆盖@k：只对「给了条件且库里有解」的题统计；空 filters 的语义题没有对错标准，")
    lines.append("  混进分母只会把分数洗白（它们恒满分）。")

    lines.append("\n## 难度分布（库内合格集规模）\n")
    lines.append("| 层 | 判得了对错的题 | 中位数 | 最小 | 最大 |")
    lines.append("|---|---|---|---|---|")
    for tier in ("literal", "mixed", "semantic", "all"):
        t = (results.get("hybrid") or {}).get("tiers", {}).get(tier) or {}
        if not t.get("judged_n"):
            continue
        lines.append(
            f"| {tier} | {t['judged_n']} | {t['eligible_med']:.0f} | "
            f"{t['eligible_min']} | {t['eligible_max']} |"
        )
    lines.append("")
    lines.append("- 合格集 = 这些数字决定了「覆盖@k」的上限，也决定了「该不该放宽」的标准答案。")

    if legs:
        lines.append("\n## 召回覆盖诊断（合格房有没有进候选池）\n")
        lines.append(f"| 层 | 题数 | dense 腿命中 | sparse 腿命中 | 任一腿命中 |")
        lines.append("|---|---|---|---|---|")
        for tier in ("literal", "mixed", "semantic", "all"):
            t = legs["tiers"].get(tier) or {}
            if not t.get("n"):
                continue
            lines.append(
                f"| {tier} | {t['n']} | {_fmt(t.get('dense'))} | {_fmt(t.get('sparse'))} | "
                f"{_fmt(t.get('either'))} |"
            )
        lines.append("")
        lines.append("- 这一列把「答不出来」拆成两半：召回腿没命中 → 候选池里根本没对的房，")
        lines.append("  改排序无用，要改候选池准入/容量；腿命中了但 top-k 没有 → 是排序的问题。")

    if conflict:
        lines.append(f"\n## 约束冲突层（{conflict['total']} 题：条件打架时该放宽并说明）\n")
        lines.append(f"- 正确率: {conflict['ok']}/{conflict['total']}（{conflict['acc']:.3f}）")
        lines.append(f"- 其中「必须触发放宽」{conflict['should_relax']} 题，其余本来能满足、不该乱放宽")
        if conflict["fails"]:
            lines.append("\n| id | 问题 | 期望放宽 | 实际放宽 | 实际召回 |")
            lines.append("|---|---|---|---|---|")
            for f in conflict["fails"]:
                got = "、".join(str(g) for g in f["got"]) or "(空)"
                lines.append(
                    f"| {f['id']} | {f['question']} | {f['expect_relaxed']} "
                    f"| {'+'.join(f['got_relaxed']) or '无'} | {got} |"
                )

    lines.append("\n## 总览\n")
    lines.append("| 模式 | strict@1（有小区名的题） | 约束满足@1 | 覆盖@k | 合格精度@k | 假放宽 |")
    lines.append("|---|---|---|---|---|---|")
    for mode, r in results.items():
        a = r["tiers"]["all"]
        lines.append(
            f"| {mode} | {_fmt(a.get('strict1'))} | {_fmt(a.get('sat1'))} | "
            f"{_fmt(a.get('cov'))} | {_fmt(a.get('precision_ok_all'))} | {_fmt(a.get('relax_bad'))} |"
        )

    lines.append("\n## 发布门禁\n")
    lines.append("| 检查项 | 阈值 | 实际 | 结论 | 含义 |")
    lines.append("|---|---|---|---|---|")
    for g in gates:
        lines.append(
            f"| {g['name']} | {g['want']} | {_fmt(g['got'])} | "
            f"{'PASS' if g['ok'] else '**FAIL**'} | {g['why']} |"
        )

    lines.append("")
    lines.append("- 注 1：题目自带 filters，评测直接喂给检索，所以这一层只衡量排序，不掺抽取。")
    lines.append("- 注 2：`hybrid` 与 `hybrid_nofilter` 在 mixed 层的差值 = 「约束排序」本身的贡献。")
    lines.append("- 注 3：strict@1 = 第一名「命中点名小区 **且** 约束全中」，判定调线上同一函数。")
    lines.append("- 注 4：索引为**全量** 38266 条房源（覆盖 4900 个小区、13355 个「小区 × 户型」组合），"
                 "不再抽样——16000 条时代有 58.2% 的房源永远检索不到"
                 "（双龙南里两居只能给 1 套 / 实际 5 套）。")
    lines.append("- 注 5：门禁阈值是**绝对**的，不再用「hybrid >= sparse」这种两边都满分、"
                 "恒为真的比法。")

    best = results.get("hybrid") or {}
    if best.get("misses"):
        lines.append("\n## hybrid 未答出合格结果（库里有解，top-k 里一条合格的都没有）\n")
        lines.append("| id | 层 | 问题 | 库内合格集 | 实际召回 | 放宽 |")
        lines.append("|---|---|---|---|---|---|")
        for m in best["misses"]:
            got = "、".join(str(g) for g in m["got"]) or "(空)"
            lines.append(
                f"| {m['id']} | {m['type']} | {m['question']} | {m['eligible_n']} | {got} | "
                f"{'+'.join(m['relaxed']) or '无'} |"
            )

    if best.get("relax_bads"):
        lines.append("\n## hybrid 假放宽（库里有合格房，却宣布放宽了条件）\n")
        lines.append("| id | 层 | 问题 | 库内合格集 | 放宽维度 | 第一名是否合格 |")
        lines.append("|---|---|---|---|---|---|")
        for m in best["relax_bads"]:
            lines.append(
                f"| {m['id']} | {m['type']} | {m['question']} | {m['eligible_n']} | "
                f"{'+'.join(m['relaxed'])} | {m['top_ok']} |"
            )

    if best.get("fails"):
        lines.append("\n## hybrid 模式失败 case（第一名未同时满足小区 + 约束）\n")
        lines.append("| id | 层 | 问题 | filters | 实际召回 | 首条软约束满足 |")
        lines.append("|---|---|---|---|---|---|")
        for f in best["fails"]:
            got = "、".join(str(g) for g in f["got"]) or "(空)"
            lines.append(
                f"| {f['id']} | {f['type']} | {f['question']} | `{json.dumps(f['filters'], ensure_ascii=False)}` "
                f"| {got} | {f['top_soft']} |"
            )

    if extraction:
        lines.append("\n## 条件抽取评测（纯正则层）\n")
        lines.append(
            f"- 整题全对: {extraction['item_ok']}/{extraction['total']}"
            f"（{extraction['item_acc']:.3f}）"
        )
        lines.append(
            f"- 字段准确率: {extraction['field_ok']}/{extraction['field_total']}"
            f"（{extraction['field_acc']:.3f}）"
        )
        if extraction["fails"]:
            lines.append("\n| id | 问题 | 偏差 |")
            lines.append("|---|---|---|")
            for f in extraction["fails"]:
                lines.append(f"| {f['id']} | {f['question']} | {'；'.join(f['bad'])} |")

    if routing:
        lines.append("\n## Supervisor 路由评测\n")
        lines.append(f"- 准确率: {routing['accuracy']:.3f} ({routing['correct']}/{routing['total']})")
        lines.append("\n| 期望 \\ 实际 | 次数 |")
        lines.append("|---|---|")
        for (exp, got, hit), cnt in sorted(routing["confusion"].items()):
            flag = "" if hit else "  ← 错"
            lines.append(f"| {exp} → {got} | {cnt}{flag} |")
        if routing["wrongs"]:
            lines.append("\n### 路由错误明细\n")
            lines.append("| id | 问题 | 期望 | 实际 | 依据 |")
            lines.append("|---|---|---|---|---|")
            for w in routing["wrongs"]:
                lines.append(f"| {w['id']} | {w['question']} | {w['expect']} | {w['got']} | {w['reason']} |")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 检索评测")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--routing", action="store_true", help="同时评测 Supervisor 路由（会调 LLM）")
    parser.add_argument("--no-extraction", action="store_true", help="跳过条件抽取评测")
    parser.add_argument("--out", default="", help="报告输出路径（默认打印到终端）")
    args = parser.parse_args()

    with open(SET_PATH, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("retrieval") or []

    graph = RAGGraph()
    k = args.top_k

    # 全库扫一遍，把每题的合格集规模算出来（题目难度底座，也是覆盖@k 的分母）
    library = load_library()
    print(f"[库内合格集] 全库 {len(library)} 条，开始按题统计…", file=sys.stderr)
    eligible_map = eligible_keys(items, library)
    for it in items:
        if it.get("type") in ("mixed", "semantic"):
            print(f"  {it['id']} 库内合格集={len(eligible_map.get(it['id']) or ())}", file=sys.stderr)

    runners = {
        "dense": lambda q, flt: dense_hits(q, flt, k),
        "sparse": lambda q, flt: sparse_hits(q, flt, k),
        "hybrid_nofilter": lambda q, flt: hybrid_hits_nofilter(graph, q, flt, k),
        "hybrid_no_div": lambda q, flt: hybrid_hits_no_div(graph, q, flt, k),
        "hybrid": lambda q, flt: hybrid_hits(graph, q, flt, k),
    }

    results = {}
    for mode, fn in runners.items():
        results[mode] = eval_retrieval(items, fn, k, eligible_map)

    legs = eval_recall_legs(graph, items, eligible_map, k)

    conflict_items = [it for it in items if it.get("type") == "conflict"]
    conflict = eval_conflict(graph, conflict_items, k) if conflict_items else None

    extraction = None
    if not args.no_extraction and EXTRACT_PATH.exists():
        with open(EXTRACT_PATH, encoding="utf-8") as f:
            extraction = eval_extraction(graph, json.load(f).get("items") or [])

    routing = None
    if args.routing:
        routing = asyncio.run(eval_routing(graph, data.get("routing") or []))

    gates = check_gates(results, legs)
    counts = Counter(it.get("type") for it in items)
    report = build_report(results, conflict, extraction, routing, legs, gates, k, counts)

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"[已写入] {out_path}")
    else:
        print(report)

    failed = [g for g in gates if not g["ok"]]
    for g in gates:
        flag = "OK  " if g["ok"] else "FAIL"
        print(f"[{flag}] {g['name']}: {_fmt(g['got'])} (要求 {g['want']})")
    if failed:
        print(f"[FAIL] {len(failed)} 项门禁未通过")
        return 1
    print("[OK] 全部门禁通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
