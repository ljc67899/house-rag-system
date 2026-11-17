# -*- coding: utf-8 -*-
"""RAG 智能问答 API"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from loguru import logger
from app.models.schemas import ChatRequest, ChatResponse, SourceReference, HouseInfo
from app.services.rag_service import RAGService
from app.api.deps import get_rag_service

router = APIRouter(prefix="/chat", tags=["智能问答"])


@router.post("/ask", response_model=ChatResponse, summary="RAG 智能问答")
async def chat_ask(
    request: ChatRequest,
    rag_service: RAGService = Depends(get_rag_service),
):
    try:
        result = await rag_service.chat(
            question=request.question,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            top_k=request.top_k,
        )
        sources = [SourceReference(**s) for s in result["sources"]]
        related_houses = [HouseInfo(**h) for h in result["related_houses"]]
        return ChatResponse(
            answer=result["answer"],
            conversation_id=result["conversation_id"],
            sources=sources,
            model=result["model"],
            latency=result["latency"],
            related_houses=related_houses,
        )
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(status_code=500, detail=f"问答处理失败: {str(e)}")


@router.post("/stream", summary="RAG 流式问答（SSE）")
async def chat_stream(
    request: ChatRequest,
    rag_service: RAGService = Depends(get_rag_service),
):
    return StreamingResponse(
        rag_service.chat_stream(
            question=request.question,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            top_k=request.top_k,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history/{conversation_id}", summary="获取会话历史")
async def get_chat_history(
    conversation_id: str,
    rag_service: RAGService = Depends(get_rag_service),
):
    history = rag_service.get_conversation_history(conversation_id)
    return {"conversation_id": conversation_id, "messages": history}
