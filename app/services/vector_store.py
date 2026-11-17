# -*- coding: utf-8 -*-
"""向量存储服务"""

import os
import threading
import uuid
from typing import List, Dict, Optional, Tuple
from loguru import logger
from app.config import settings
from app.core.exceptions import VectorStoreError


class VectorStoreService:
    # Chroma 的 get() 一旦不带 limit，就会把命中 id 全部拼进 SQL；库内条数超过
    # SQLite 参数上限时直接抛 "too many SQL variables"，而下面每个方法都用
    # `except Exception` 兜底返回空 —— 症状是静默的：BM25 稀疏路建出空索引、
    # 混合检索退化成纯稠密、启动校验把 38266 条看成 0 条。
    # 索引从 16000 扩到全量 38266 之后这个坑立刻就踩到了，所以全量扫描一律分页。
    _PAGE_SIZE = 500

    def __init__(self):
        self.collection_name = settings.vector_db_collection
        self._client = None
        self._collection = None
        # filter_type -> (集合总条数, 该类型的条数)：分页统计一次要 8~9s，
        # 而 BM25 每次取数都拿它当索引版本号，必须缓存（重建/增删时清空）
        self._count_cache: Dict[str, Tuple[int, int]] = {}
        # 预热线程与首个请求可能同时来统计，不锁就会重复扫两遍全库
        self._count_lock = threading.Lock()
        # 同理会并发初始化：两个线程同时 PersistentClient 会互相踩
        # （实测报 Could not connect to tenant / RustBindingsAPI 无 bindings），
        # 而调用方都是 except 兜底 → BM25 静默退回单路稠密，只留一行 warning。
        self._init_lock = threading.Lock()

    def _paged_get(self, where=None, include=("metadatas",), limit: int = None):
        """分页扫全量匹配结果，逐页 yield Chroma 原始返回

        include=[] 只取 id（统计用，38266 条约 5s）；要正文/元数据时按需传，
        一次性取全量会占几十 MB 内存，BM25 建库本来就需要，属于刚性开销。
        """
        offset = 0
        while True:
            size = self._PAGE_SIZE
            if limit:
                size = min(size, limit - offset)
                if size <= 0:
                    return
            page = self._collection.get(
                where=where, include=list(include), limit=size, offset=offset
            )
            ids = list(page.get("ids") or [])
            if not ids:
                return
            yield page
            if len(ids) < size:
                return
            offset += len(ids)

    def _lazy_init(self):
        if self._client is not None:
            return
        with self._init_lock:
            if self._client is not None:      # 等锁期间别的线程已经初始化好了
                return
            try:
                import chromadb
                from chromadb.config import Settings as ChromaSettings
                os.makedirs(settings.vector_db_path, exist_ok=True)
                client = chromadb.PersistentClient(
                    path=settings.vector_db_path,
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
                collection = client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
                # 全部成功后再赋值，避免半初始化状态被别的线程看到
                self._collection = collection
                self._client = client
                logger.info(f"ChromaDB initialized, collection: {self.collection_name}")
            except Exception as e:
                logger.error(f"Failed to initialize vector store: {e}")
                raise VectorStoreError(f"向量数据库初始化失败: {e}")

    @property
    def collection(self):
        self._lazy_init()
        return self._collection

    def add_houses(self, house_docs, embeddings, metadatas):
        self._lazy_init()
        try:
            ids = [f"house_{meta.get('house_id', uuid.uuid4().hex)}_{uuid.uuid4().hex[:8]}"
                   for meta in metadatas]
            for meta in metadatas:
                meta["type"] = "house"
            self._collection.add(ids=ids, embeddings=embeddings, documents=house_docs, metadatas=metadatas)
            self._count_cache.clear()
            logger.info(f"Added {len(house_docs)} house vectors")
            return ids
        except Exception as e:
            logger.error(f"Failed to add house vectors: {e}")
            raise VectorStoreError(f"添加房源向量失败: {e}")

    def add_documents(self, docs, embeddings, metadatas, doc_id):
        self._lazy_init()
        try:
            ids = [f"doc_{doc_id}_{i}_{uuid.uuid4().hex[:8]}" for i in range(len(docs))]
            for meta in metadatas:
                meta["type"] = "document"
                meta["doc_id"] = doc_id
            self._collection.add(ids=ids, embeddings=embeddings, documents=docs, metadatas=metadatas)
            self._count_cache.clear()
            logger.info(f"Added {len(docs)} document vectors")
            return ids
        except Exception as e:
            logger.error(f"Failed to add document vectors: {e}")
            raise VectorStoreError(f"添加文档向量失败: {e}")

    def search(self, query_embedding, top_k=5, score_threshold=None, filter_type=None):
        self._lazy_init()
        try:
            filter_meta = {"type": filter_type} if filter_type else None
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=filter_meta,
            )
            documents = results["documents"][0] if results["documents"] else []
            metadatas = results["metadatas"][0] if results["metadatas"] else []
            distances = results["distances"][0] if results["distances"] else []
            scores = [1.0 - d for d in distances]

            if score_threshold is not None:
                filtered = [(doc, meta, score)
                    for doc, meta, score in zip(documents, metadatas, scores)
                    if score >= score_threshold]
                if filtered:
                    documents, metadatas, scores = zip(*filtered)
                    documents = list(documents)
                    metadatas = list(metadatas)
                    scores = list(scores)
                else:
                    documents, metadatas, scores = [], [], []
            return documents, metadatas, scores
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            raise VectorStoreError(f"向量检索失败: {e}")

    def get_entries(self, filter_type: str = None, limit: int = None):
        """按类型取出向量 id 与元数据（用于统计、迁移、重建）

        返回 (ids, metadatas)
        """
        self._lazy_init()
        try:
            where = {"type": filter_type} if filter_type else None
            ids, metadatas = [], []
            for page in self._paged_get(where, include=("metadatas",), limit=limit):
                ids.extend(page.get("ids") or [])
                metadatas.extend(page.get("metadatas") or [])
            return ids, metadatas
        except Exception as e:
            logger.error(f"Failed to get vector entries: {e}")
            return [], []

    def get_all_documents(self, filter_type: str = None, limit: int = None) -> Dict[str, Tuple[str, dict]]:
        """按类型取回全量「正文 + 元数据」，供稀疏索引（BM25）建库

        与 get_entries 的区别：这里把 documents 一起取出来。只取文本，
        不取 embedding——稀疏路不需要向量，也就不会多占内存。

        返回 {向量 id: (正文, 元数据)}
        """
        self._lazy_init()
        try:
            where = {"type": filter_type} if filter_type else None
            out: Dict[str, Tuple[str, dict]] = {}
            for page in self._paged_get(where, include=("documents", "metadatas"), limit=limit):
                for vector_id, doc, meta in zip(
                    page.get("ids") or [],
                    page.get("documents") or [],
                    page.get("metadatas") or [],
                ):
                    out[vector_id] = (doc or "", meta or {})
            return out
        except Exception as e:
            logger.error(f"Failed to get all documents: {e}")
            return {}

    def get_ids_where(self, where: dict = None) -> List[str]:
        """按任意 metadata 条件取向量 id（例如定位某个 house_id 对应的向量）"""
        self._lazy_init()
        try:
            ids: List[str] = []
            for page in self._paged_get(where or None, include=()):
                ids.extend(page.get("ids") or [])
            return ids
        except Exception as e:
            logger.error(f"Failed to query vector ids: {e}")
            return []

    def delete_ids(self, ids) -> int:
        """按向量 id 批量删除（重建索引时清理旧数据用）"""
        self._lazy_init()
        ids = list(ids or [])
        if not ids:
            return 0
        try:
            self._collection.delete(ids=ids)
            self._count_cache.clear()
            logger.info(f"Deleted {len(ids)} vectors")
            return len(ids)
        except Exception as e:
            logger.error(f"Failed to delete vectors: {e}")
            return 0

    def count(self, filter_type: str = None) -> int:
        self._lazy_init()
        try:
            total = self._collection.count()   # 0.01s 级，可当缓存签名
            if not filter_type:
                return total
            sig, cached = self._count_cache.get(filter_type, (-1, 0))
            if sig == total:
                return cached
            with self._count_lock:
                # 等锁期间别人可能刚统计完（启动预热 + 首个请求）
                sig, cached = self._count_cache.get(filter_type, (-1, 0))
                if sig == total:
                    return cached
                n = 0
                for page in self._paged_get({"type": filter_type}, include=()):
                    n += len(page.get("ids") or [])
                self._count_cache[filter_type] = (total, n)
                return n
        except Exception as e:
            logger.error(f"Failed to count vectors: {e}")
            return 0

    def health_check(self) -> bool:
        try:
            self._lazy_init()
            _ = self._collection.count()
            return True
        except Exception as e:
            logger.error(f"Vector store health check failed: {e}")
            return False


_vector_store: Optional[VectorStoreService] = None

def get_vector_store() -> VectorStoreService:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStoreService()
    return _vector_store
