# -*- coding: utf-8 -*-
"""站内消息 API

全部需要登录（require_user）：
    GET  /notifications                  消息列表（分页 + 未读数）
    GET  /notifications/unread-count     未读数（前端轮询角标）
    POST /notifications/{id}/read        单条已读
    POST /notifications/read-all         全部已读
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.database import get_db
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["站内消息"])


@router.get("", summary="消息列表")
def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    payload: dict = Depends(require_user),
):
    items, total, unread = NotificationService(db).list(
        int(payload["sub"]), page=page, page_size=page_size)
    return {
        "items": items,
        "total": total,
        "unread": unread,
        "page": page,
        "page_size": page_size,
    }


@router.get("/unread-count", summary="未读消息数")
def unread_count(
    db: Session = Depends(get_db),
    payload: dict = Depends(require_user),
):
    return {"unread": NotificationService(db).unread_count(int(payload["sub"]))}


@router.post("/read-all", summary="全部标记为已读")
def read_all(
    db: Session = Depends(get_db),
    payload: dict = Depends(require_user),
):
    changed = NotificationService(db).mark_all_read(int(payload["sub"]))
    return {"message": "已全部标记为已读", "changed": changed}


@router.post("/{notification_id}/read", summary="单条标记为已读")
def read_one(
    notification_id: int,
    db: Session = Depends(get_db),
    payload: dict = Depends(require_user),
):
    ok = NotificationService(db).mark_read(int(payload["sub"]), notification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"message": "已读"}
