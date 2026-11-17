# -*- coding: utf-8 -*-
"""知识库管理服务

设计要点：
    1. 「有哪些文档、每个文档多少切片」以向量库为准（type=document 的 metadata.doc_id 分组），
       因为只有向量库才代表「AI 真的能检索到什么」；
    2. 「文件大小 / 上传时间 / 上传人」这类管理信息存在 knowledge_document 表；
    3. 两边用 doc_id 关联，缺失一方也能正常展示（历史脏数据不会让接口报错）：
          有向量无记录 -> 文件名取向量里的 document_name，大小为 0
          有记录无向量 -> 状态标记 missing，提示需要重新上传
    4. 删除时先删向量再删文件和记录，任一步失败都会返回明确结果，便于前端提示。
"""

import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy.orm import Session

from app.config import settings
from app.models.knowledge import KnowledgeDocument
from app.services.vector_store import get_vector_store


class KnowledgeService:
    def __init__(self, db: Session):
        self.db = db
        self.store = get_vector_store()

    # ---------- 向量库扫描 ----------

    def _scan_vector_docs(self) -> Dict[str, dict]:
        """按 doc_id 聚合文档切片，返回 {doc_id: {document_name, chunk_count}}"""
        groups: Dict[str, dict] = {}
        ids, metadatas = self.store.get_entries("document")
        for _vid, meta in zip(ids, metadatas):
            meta = meta or {}
            doc_id = str(meta.get("doc_id") or "unknown")
            group = groups.setdefault(
                doc_id,
                {"doc_id": doc_id, "document_name": meta.get("document_name"), "chunk_count": 0},
            )
            group["chunk_count"] += 1
            if not group["document_name"] and meta.get("document_name"):
                group["document_name"] = meta.get("document_name")
        return groups

    # ---------- 列表 / 统计 ----------

    def list_documents(
        self, page: int = 1, page_size: int = 10, keyword: Optional[str] = None
    ) -> Tuple[List[dict], int]:
        groups = self._scan_vector_docs()
        rows = {r.doc_id: r for r in self.db.query(KnowledgeDocument).all() if r.doc_id}

        items: List[dict] = []
        for doc_id in set(groups) | set(rows):
            group = groups.get(doc_id) or {}
            row = rows.get(doc_id)
            filename = (row.filename if row else None) or group.get("document_name") or f"{doc_id[:8]}…"
            items.append({
                "doc_id": doc_id,
                "filename": filename,
                "file_type": (row.file_type if row else None) or "unknown",
                "file_size": int(row.file_size or 0) if row else 0,
                "char_count": int(row.char_count or 0) if row else 0,
                "chunk_count": int(group.get("chunk_count") or 0),
                "uploader": row.uploader if row else None,
                "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if row and row.create_time else None,
                "status": "ready" if group.get("chunk_count") else "missing",
            })

        if keyword:
            kw = keyword.strip().lower()
            items = [i for i in items if kw in str(i["filename"]).lower()]

        # 上传时间倒序，没有时间的排最后（历史数据 / 只有向量的文档）
        items.sort(
            key=lambda i: (i["create_time"] or "", i["filename"]),
            reverse=True,
        )
        total = len(items)
        start = max(0, (page - 1) * page_size)
        return items[start:start + page_size], total

    def get_stats(self) -> dict:
        groups = self._scan_vector_docs()
        rows = self.db.query(KnowledgeDocument).all()
        doc_ids = set(groups) | {r.doc_id for r in rows if r.doc_id}
        return {
            "document_count": len(doc_ids),
            "chunk_count": sum(g.get("chunk_count") or 0 for g in groups.values()),
            "total_size": sum(int(r.file_size or 0) for r in rows),
            "house_vectors": self.store.count("house"),
            "collection_total": self.store.count(),
            "chunk_size": settings.rag_chunk_size,
            "chunk_overlap": settings.rag_chunk_overlap,
        }

    # ---------- 写入 ----------

    def record_document(
        self,
        doc_id: str,
        filename: str,
        file_type: str,
        file_size: int,
        char_count: int,
        chunk_count: int,
        file_path: str,
        uploader: Optional[str] = None,
    ) -> KnowledgeDocument:
        """上传成功后记录文档元信息（同一 doc_id 重复上传则覆盖）"""
        row = self.db.query(KnowledgeDocument).filter(KnowledgeDocument.doc_id == doc_id).first()
        if row is None:
            row = KnowledgeDocument(doc_id=doc_id)
            self.db.add(row)
        row.filename = filename
        row.file_type = file_type
        row.file_size = file_size
        row.char_count = char_count
        row.chunk_count = chunk_count
        row.file_path = file_path
        row.status = "completed"
        row.uploader = uploader
        row.create_time = datetime.now()
        self.db.commit()
        self.db.refresh(row)
        return row

    # ---------- 删除 ----------

    def delete_document(self, doc_id: str) -> dict:
        """删除文档：向量 -> 落盘文件 -> 元信息记录"""
        ids = self.store.get_ids_where({"doc_id": doc_id})
        deleted_vectors = self.store.delete_ids(ids)

        row = self.db.query(KnowledgeDocument).filter(KnowledgeDocument.doc_id == doc_id).first()
        file_removed = False
        if row and row.file_path:
            file_removed = self._safe_remove(row.file_path)

        if row:
            self.db.delete(row)
            self.db.commit()

        logger.info(
            f"知识文档已删除: doc_id={doc_id}, vectors={deleted_vectors}, file_removed={file_removed}"
        )
        return {
            "doc_id": doc_id,
            "deleted_vectors": deleted_vectors,
            "file_removed": file_removed,
            "existed": bool(row) or deleted_vectors > 0,
        }

    def _safe_remove(self, file_path: str) -> bool:
        """只允许删除文档存储目录内的文件，防止路径穿越误删"""
        try:
            base = os.path.abspath(settings.document_storage_path)
            target = os.path.abspath(file_path)
            if os.path.commonpath([base, target]) != base:
                logger.warning(f"拒绝删除文档目录外的文件: {target}")
                return False
            if os.path.exists(target):
                os.remove(target)
                return True
        except Exception as e:
            logger.warning(f"删除文档文件失败: {file_path} -> {e}")
        return False

    # ---------- 房源索引重建 ----------

    def rebuild_house_index(self, limit: int = None) -> dict:
        """重建房源向量索引（按区域均衡抽样），与知识文档互不影响"""
        from app.services.index_sync import IndexSyncService

        return IndexSyncService(self.db).rebuild(limit=limit)


def get_knowledge_service(db: Session) -> KnowledgeService:
    return KnowledgeService(db)


__all__ = ["KnowledgeService", "get_knowledge_service"]
