# -*- coding: utf-8 -*-
"""API 路由聚合：统一暴露 api_router，入口程序只需挂载一次"""

from fastapi import APIRouter

from app.api.routes.house import router as house_router
from app.api.routes.chat import router as chat_router
from app.api.routes.knowledge import router as knowledge_router
from app.api.routes.user import router as user_router
from app.api.routes.analysis import router as analysis_router
from app.api.routes.admin import router as admin_router
from app.api.routes.notification import router as notification_router

api_router = APIRouter()
api_router.include_router(house_router)
api_router.include_router(chat_router)
api_router.include_router(knowledge_router)
api_router.include_router(user_router)
api_router.include_router(analysis_router)
api_router.include_router(admin_router)
api_router.include_router(notification_router)

__all__ = [
    "api_router",
    "house_router",
    "chat_router",
    "knowledge_router",
    "user_router",
    "analysis_router",
    "admin_router",
    "notification_router",
]
