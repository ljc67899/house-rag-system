# -*- coding: utf-8 -*-
"""LangChain 适配层

把项目里已有的 Embedding / 向量库封装成 LangChain 标准接口：
    - HouseEmbeddings  -> langchain_core.embeddings.Embeddings
    - HouseVectorStore -> langchain_core.vectorstores.VectorStore

这样上层（Retriever / LCEL 链 / LangGraph 节点）可以直接使用 LangChain 生态能力，
同时底层仍然复用已经建好的 ChromaDB 向量（无需重建，维度与归一化方式完全一致）。
"""

from typing import List, Optional, Tuple

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
from loguru import logger

from app.services.embedding_service import get_text_embedding_service
from app.services.vector_store import get_vector_store


class HouseEmbeddings(Embeddings):
    """LangChain Embeddings 适配器，底层复用 TextEmbeddingService

    注意：必须复用同一个 embedding 服务，否则查询向量与库中向量不在同一空间。
    """

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return get_text_embedding_service().encode(list(texts))

    def embed_query(self, text: str) -> List[float]:
        return get_text_embedding_service().encode_query(text)


class HouseVectorStore(VectorStore):
    """LangChain VectorStore 适配器，底层复用 ChromaDB 封装

    doc_type="house"     -> 只检索房源向量
    doc_type="document"  -> 只检索知识文档向量
    """

    def __init__(self, embeddings: Optional[Embeddings] = None, doc_type: str = "house"):
        self._embeddings = embeddings or HouseEmbeddings()
        self._store = get_vector_store()
        self._doc_type = doc_type

    @property
    def embeddings(self) -> Embeddings:
        return self._embeddings

    @classmethod
    def from_texts(cls, texts, embedding, metadatas=None, **kwargs) -> "HouseVectorStore":
        """LangChain VectorStore 抽象类要求的构造入口"""
        store = cls(embeddings=embedding, doc_type=kwargs.get("doc_type", "house"))
        store.add_texts(texts, metadatas=metadatas, **kwargs)
        return store

    # ---------- 写入 ----------

    def add_texts(self, texts, metadatas=None, **kwargs) -> List[str]:
        texts = list(texts)
        metas = list(metadatas) if metadatas else [{} for _ in texts]
        vectors = self._embeddings.embed_documents(texts)

        doc_type = kwargs.get("doc_type", self._doc_type)
        if doc_type == "document":
            return self._store.add_documents(
                docs=texts, embeddings=vectors, metadatas=metas,
                doc_id=kwargs.get("doc_id", "default"),
            )
        return self._store.add_houses(house_docs=texts, embeddings=vectors, metadatas=metas)

    # ---------- 检索 ----------

    def similarity_search(self, query: str, k: int = 4, **kwargs) -> List[Document]:
        pairs = self.similarity_search_with_score(query, k=k, **kwargs)
        return [doc for doc, _ in pairs]

    def similarity_search_with_score(
        self, query: str, k: int = 4, **kwargs
    ) -> List[Tuple[Document, float]]:
        return self.similarity_search_with_score_by_vector(
            self._embeddings.embed_query(query), k=k, **kwargs
        )

    def similarity_search_with_score_by_vector(
        self, embedding: List[float], k: int = 4, **kwargs
    ) -> List[Tuple[Document, float]]:
        filter_type = kwargs.get("filter_type", self._doc_type)
        score_threshold = kwargs.get("score_threshold")

        docs, metas, scores = self._store.search(
            query_embedding=embedding,
            top_k=k,
            score_threshold=score_threshold,
            filter_type=filter_type,
        )
        return [
            (Document(page_content=doc, metadata=meta or {}), float(score))
            for doc, meta, score in zip(docs, metas, scores)
        ]


# ---------- 工厂函数 ----------

_house_store: Optional[HouseVectorStore] = None
_doc_store: Optional[HouseVectorStore] = None


def get_house_store() -> HouseVectorStore:
    """房源向量库（对应 LangChain Retriever 的房源检索）"""
    global _house_store
    if _house_store is None:
        _house_store = HouseVectorStore(doc_type="house")
        logger.info("LangChain HouseVectorStore initialized")
    return _house_store


def get_doc_store() -> HouseVectorStore:
    """知识文档向量库"""
    global _doc_store
    if _doc_store is None:
        _doc_store = HouseVectorStore(doc_type="document")
        logger.info("LangChain DocVectorStore initialized")
    return _doc_store


__all__ = [
    "HouseEmbeddings",
    "HouseVectorStore",
    "get_house_store",
    "get_doc_store",
]
