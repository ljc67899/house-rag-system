# -*- coding: utf-8 -*-
"""房源库稀疏检索（BM25）—— 与稠密向量路组成真正的混合召回

为什么需要它：
    只有稠密向量的检索器，对「小区名」「地铁路线」这类**字面精确**的查询是吃亏的：
    embedding 会把「首邑溪谷」「14 号线」压成语义近似向量，问「首邑溪谷」时
    可能召回一堆「XX 谷」的房源，而真正那个小区排不到第一。
    BM25 正好相反，字面命中就是命中。两者名次做 RRF 融合，比任何单路都稳。

为什么不用 jieba + rank_bm25：
    多一个分词依赖就多一份装不上、分不对的风险，而且房源文本里「朝阳」「两居」
    「地铁」这类词用**字符 bigram** 就能切出来，中文场景实测够用。零新依赖。

为什么带字段权重：
    「小区：首邑溪谷」里的「首邑溪谷」远比「房屋优势：采光好」重要。
    所以按 `to_search_doc()` 的 `字段：值` 结构分别加权，而不是把整段拍平。

三条约束：
    1. 索引按向量库规模做版本缓存，重建索引后 count 变化会自动失效重建；
    2. 建库失败返回 None，检索自动退回单路稠密，绝不拖垮问答主流程；
    3. 只做召回排名，不产出业务结论——所有分数都交给 RRF 只看名次。
"""

import math
import re
import threading
from typing import Dict, List, Optional, Tuple

from loguru import logger

# ---------- BM25 超参（业界默认值，未针对本项目调参） ----------
_K1 = 1.5
_B = 0.75

# 中文单字权重：以 bigram 为主，单字只降权保留（避免「的」「区」这类高频字刷分）
_UNIGRAM_WEIGHT = 0.3

# 字段权重：小区/标题/区域是用户最常问的字面锚点，描述类字段权重压低
_FIELD_WEIGHTS = {
    "房源标题": 1.6,
    "小区": 2.5,
    "区域": 1.8,
    "街道": 1.6,
    "交通": 1.6,
    "户型": 1.2,
    "房屋类型": 1.0,
    "朝向": 0.8,
    "房屋优势": 0.8,
    "配套设施": 0.6,
    "周边配套": 0.6,
    "价格": 0.5,
    "面积": 0.5,
}
_DEFAULT_FIELD_WEIGHT = 1.0

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[a-z0-9]+")

# 向量库规模 -> BM25Index 的缓存（规模变了说明重建过索引）
_CACHE: Dict[str, Tuple[int, Optional["BM25Index"]]] = {}

# 全量 38266 条建一次约 23s。启动预热线程和第一个用户请求很容易同时进来，
# 不加锁就各建一份：白烧 CPU，内存也翻倍。
_BUILD_LOCK = threading.Lock()


def _weighted_terms(text: str) -> Dict[str, float]:
    """把 `字段：值；字段：值` 结构的房源文档切成 {词: 加权词频}

    token 组成：
        中文连续段 -> 字符 bigram（权重 1×字段权重）+ 单字（权重 0.3×字段权重）
        英文/数字 -> 整个 token（「14」「号线」里的数字、小区英文名）
    """
    terms: Dict[str, float] = {}
    for field in (text or "").split("；"):
        field = field.strip()
        if not field:
            continue
        if "：" in field:
            name, value = field.split("：", 1)
        else:
            name, value = "", field
        weight = _FIELD_WEIGHTS.get(name.strip(), _DEFAULT_FIELD_WEIGHT)
        lowered = value.lower()

        for seg in _CJK_RE.findall(lowered):
            if len(seg) == 1:
                terms[seg] = terms.get(seg, 0.0) + weight * _UNIGRAM_WEIGHT
                continue
            for i in range(len(seg) - 1):
                bigram = seg[i:i + 2]
                terms[bigram] = terms.get(bigram, 0.0) + weight

        for token in _LATIN_RE.findall(lowered):
            terms[token] = terms.get(token, 0.0) + weight
    return terms


class BM25Index:
    """内存版 BM25（语料只有几百到几千条房源，全量扫一遍足够快）"""

    def __init__(self, docs: Dict[str, Tuple[str, dict]]):
        self.raw: Dict[str, Tuple[str, dict]] = docs
        self.keys: List[str] = list(docs.keys())
        self.term_freqs: List[Dict[str, float]] = []
        self.doc_lens: List[float] = []

        df: Dict[str, int] = {}
        for key in self.keys:
            terms = _weighted_terms(docs[key][0])
            self.term_freqs.append(terms)
            self.doc_lens.append(sum(terms.values()) or 1.0)
            for term in terms:
                df[term] = df.get(term, 0) + 1

        self.df = df
        self.n = len(self.keys)
        self.avgdl = (sum(self.doc_lens) / self.n) if self.n else 0.0
        self._idf_cache: Dict[str, float] = {}

    def _idf(self, term: str) -> float:
        value = self._idf_cache.get(term)
        if value is None:
            df = self.df.get(term, 0)
            # 带 +0.5 平滑的 IDF，避免长尾词出现负分
            value = math.log(1 + (self.n - df + 0.5) / (df + 0.5)) if self.n else 0.0
            self._idf_cache[term] = value
        return value

    def search(self, query: str, k: int = 20) -> List[Tuple[str, float]]:
        """返回 [(候选 key, BM25 分)]，已按分降序、只保留有命中的"""
        query_terms = _weighted_terms(query)
        if not query_terms or not self.n:
            return []

        results: List[Tuple[str, float]] = []
        for i, term_freq in enumerate(self.term_freqs):
            dl = self.doc_lens[i]
            length_norm = _K1 * (1 - _B + _B * (dl / self.avgdl if self.avgdl else 0.0))
            score = 0.0
            for term in query_terms:
                freq = term_freq.get(term)
                if not freq:
                    continue
                score += self._idf(term) * (freq * (_K1 + 1)) / (freq + length_norm)
            if score > 0:
                results.append((self.keys[i], score))

        results.sort(key=lambda item: -item[1])
        return results[:k]


def doc_key(metadata: dict) -> str:
    """候选统一 key：RRF 要靠它对齐稠密路与稀疏路的结果"""
    house_id = (metadata or {}).get("house_id")
    return f"house:{house_id}" if house_id else ""


def get_house_bm25() -> Optional[BM25Index]:
    """按向量库当前规模缓存房源 BM25 索引；建不起来就返回 None（上层退回单路）"""
    from app.services.vector_store import get_vector_store

    try:
        store = get_vector_store()
        version = store.count("house")
    except Exception as exc:
        logger.warning(f"[稀疏检索] 读取向量库规模失败，跳过 BM25: {exc}")
        return None

    if not version:
        return None

    cached = _CACHE.get("house")
    if cached and cached[0] == version:
        return cached[1]

    with _BUILD_LOCK:
        # 双检查：等锁期间预热线程可能已经把索引建好了
        cached = _CACHE.get("house")
        if cached and cached[0] == version:
            return cached[1]

        try:
            entries = store.get_all_documents("house")
        except Exception as exc:
            logger.warning(f"[稀疏检索] 取房源全量文本失败，跳过 BM25: {exc}")
            _CACHE["house"] = (version, None)
            return None

        docs: Dict[str, Tuple[str, dict]] = {}
        for vector_id, (content, metadata) in entries.items():
            key = doc_key(metadata)
            if not key or not content:
                continue
            # 同一房源理论上只有一条向量（重建是「先写新、再删旧」），重复时保留首条
            docs.setdefault(key, (content, metadata))

        index = BM25Index(docs) if docs else None
        _CACHE["house"] = (version, index)
        if index:
            logger.info(f"[稀疏检索] 房源 BM25 索引就绪: {index.n} 条, 平均长度 {index.avgdl:.1f}")
        return index


def invalidate_house_bm25() -> None:
    """重建索引后主动失效（count 恰好不变的极端情况兜底）"""
    _CACHE.pop("house", None)


__all__ = ["BM25Index", "doc_key", "get_house_bm25", "invalidate_house_bm25"]
