# -*- coding: utf-8 -*-
"""知识库管理 API

接口分两类：
    1. 管理接口（需 knowledge:manage 权限，仅管理员）
       - POST   /knowledge/upload              上传 pdf/docx/txt/md，解析 + 分块 + 向量化
       - GET    /knowledge/documents            文档列表（含切片数）
       - DELETE /knowledge/documents/{doc_id}   删除文档（向量 + 文件 + 元信息）
       - POST   /knowledge/index-houses         重建房源向量索引
    2. 公开接口
       - GET    /knowledge/stats               知识库统计（文档数 / 切片数），供前台展示

说明：
    上传写入的向量 metadata.type=document，与房源向量（type=house）互不干扰，
    RAG 问答时走 get_doc_store() 单独检索这一路，所以这里删掉的文档会立刻从问答来源中消失。
"""

import os
import uuid

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from loguru import logger
from sqlalchemy.orm import Session

from app.api.deps import get_rag_service, require_permission
from app.config import settings
from app.database import get_db
from app.services.document_service import get_document_service
from app.services.knowledge_service import KnowledgeService
from app.services.rag_service import RAGService

router = APIRouter(prefix="/knowledge", tags=["知识库管理"])

MANAGE_PERMISSION = "knowledge:manage"


@router.get("/stats", summary="知识库统计")
async def knowledge_stats(db: Session = Depends(get_db)):
    """公开统计：已索引文档数、切片总数、房源向量数（前台展示「AI 已索引 N 篇」）"""
    return KnowledgeService(db).get_stats()


@router.get("/documents", summary="知识文档列表")
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: str = Query("", description="按文件名模糊搜索"),
    payload: dict = Depends(require_permission(MANAGE_PERMISSION)),
    db: Session = Depends(get_db),
):
    """文档列表：切片数以向量库实际数据为准，缺元信息的历史文档也能正常展示"""
    items, total = KnowledgeService(db).list_documents(page, page_size, keyword)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/upload", summary="上传知识文档")
async def upload_knowledge(
    file: UploadFile = File(...),
    payload: dict = Depends(require_permission(MANAGE_PERMISSION)),
    db: Session = Depends(get_db),
    rag_service: RAGService = Depends(get_rag_service),
):
    """上传并索引知识文档：保存文件 -> 解析正文 -> 分块向量化 -> 记录元信息"""
    try:
        filename = file.filename or ""
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in settings.allowed_extensions_set:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型: {ext or '未知'}，仅支持 {settings.allowed_extensions}",
            )

        doc_id = uuid.uuid4().hex
        saved_filename = f"{doc_id}{ext}"
        saved_path = os.path.join(settings.document_storage_path, saved_filename)
        os.makedirs(settings.document_storage_path, exist_ok=True)

        file_size = 0
        async with aiofiles.open(saved_path, "wb") as f:
            while chunk := await file.read(8192):
                file_size += len(chunk)
                if file_size > settings.max_upload_size:
                    raise HTTPException(status_code=413, detail="文件过大")
                await f.write(chunk)

        doc_service = get_document_service()
        try:
            content, file_type = doc_service.parse(saved_path)
        except Exception as e:
            # 解析失败时把刚落盘的文件清掉，避免留下垃圾文件
            try:
                os.remove(saved_path)
            except OSError:
                pass
            raise HTTPException(status_code=400, detail=f"文档解析失败: {e}")

        if not content or not content.strip():
            try:
                os.remove(saved_path)
            except OSError:
                pass
            raise HTTPException(status_code=400, detail="文档内容为空，无法建立索引")

        result = await rag_service.index_document(
            content=content, document_name=filename, doc_id=doc_id,
        )
        KnowledgeService(db).record_document(
            doc_id=doc_id,
            filename=filename,
            file_type=file_type,
            file_size=file_size,
            char_count=len(content),
            chunk_count=int(result.get("chunk_count") or 0),
            file_path=saved_path,
            uploader=payload.get("name") or "admin",
        )
        return {
            "doc_id": doc_id, "filename": filename,
            "file_type": file_type, "file_size": file_size,
            "char_count": len(content),
            "chunk_count": result["chunk_count"], "status": result["status"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload knowledge failed: {e}")
        raise HTTPException(status_code=500, detail=f"文档上传失败: {str(e)}")


@router.delete("/documents/{doc_id}", summary="删除知识文档")
async def delete_document(
    doc_id: str,
    payload: dict = Depends(require_permission(MANAGE_PERMISSION)),
    db: Session = Depends(get_db),
):
    """删除文档：同时清理向量切片与落盘文件，删除后 AI 问答不再引用它"""
    result = KnowledgeService(db).delete_document(doc_id)
    if not result["existed"]:
        raise HTTPException(status_code=404, detail="文档不存在或已被删除")
    return {
        "success": True,
        "message": f"已删除，清理 {result['deleted_vectors']} 个向量切片",
        **result,
    }


@router.post("/index-houses", summary="重建房源向量索引（按区域均衡抽样）")
async def index_houses(
    limit: int = Query(40000, ge=100, le=100000, description="抽样总条数，按区域平均分配"),
    payload: dict = Depends(require_permission(MANAGE_PERMISSION)),
    db: Session = Depends(get_db),
):
    try:
        result = KnowledgeService(db).rebuild_house_index(limit=limit)
        return {
            "message": (
                f"索引重建完成：{result['indexed']} 条房源，"
                f"覆盖 {result['region_count']} 个区域（每区 {result['per_region']} 条）"
            ),
            "count": result["indexed"],
            **result,
        }
    except Exception as e:
        logger.error(f"Index houses failed: {e}")
        raise HTTPException(status_code=500, detail=f"索引失败: {str(e)}")
