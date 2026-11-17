# -*- coding: utf-8 -*-
"""Cross-Encoder 精排（rerank）

为什么召回之后还要精排：
    召回阶段（向量 / BM25）为了快，都是把 query 和 doc 分别编码再算相似度，
    query 与 doc 之间**没有交互**。cross-encoder 把「query + doc」拼成一个输入
    过一遍模型，能捕捉「问了什么」和「这条里有没有」之间的细粒度匹配，
    因此能把「语义相似但没答到点上」的候选压下去。

为什么它不是 RRF 的替代品：
    RRF 在召回层解决「稠密和稀疏该信谁」，只看名次、不看内容；
    rerank 在召回之后解决「这些候选里到底哪条最相关」，看的是内容。
    两者是流水线的不同环节，本项目两段都做。

工程约束：
    1. 模型懒加载，首次调用约 1-2 秒；
    2. 加载失败 / 推理异常 → 返回 None，上层自动退回融合排序，绝不拖垮问答；
    3. CPU 上 20 条候选约 200-400ms，因此只对融合后的 top_n 做精排。
"""

from typing import List, Optional, Sequence, Tuple

from langchain_core.documents import Document
from loguru import logger

from app.config import settings


class RerankerService:
    """bge-reranker 系列 cross-encoder 封装（失败静默降级）"""

    def __init__(self) -> None:
        self.model_name = settings.rag_rerank_model
        self.enabled = settings.rag_rerank_enabled
        self.device = settings.embedding_device
        self._model = None
        self._failed = False

    @property
    def available(self) -> bool:
        return bool(self.enabled and not self._failed)

    def _lazy_load(self):
        if self._model is not None:
            return self._model
        from sentence_transformers import CrossEncoder

        logger.info(f"[精排] 加载 cross-encoder: {self.model_name}")
        self._model = CrossEncoder(self.model_name, device=self.device, max_length=512)
        logger.info("[精排] cross-encoder 就绪")
        return self._model

    def rerank(
        self,
        query: str,
        docs: Sequence[Document],
        top_n: Optional[int] = None,
    ) -> Optional[List[Tuple[Document, float]]]:
        """返回按相关性降序的 [(doc, score)]；不可用时返回 None（调用方保留原顺序）"""
        if not self.available or not docs:
            return None

        try:
            model = self._lazy_load()
            pairs = [(query, doc.page_content or "") for doc in docs]
            scores = model.predict(pairs, batch_size=8, show_progress_bar=False)
            ranked = sorted(
                zip(docs, scores), key=lambda item: -float(item[1])
            )
            result = [(doc, float(score)) for doc, score in ranked]
            return result[:top_n] if top_n else result
        except Exception as exc:  # 下载失败 / 内存不足 / 模型不兼容
            self._failed = True
            logger.warning(f"[精排] 不可用，退回融合排序: {exc}")
            return None


_reranker: Optional[RerankerService] = None


def get_reranker() -> RerankerService:
    global _reranker
    if _reranker is None:
        _reranker = RerankerService()
    return _reranker


__all__ = ["RerankerService", "get_reranker"]
