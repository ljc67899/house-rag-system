# -*- coding: utf-8 -*-
"""数据库连接与基础配置（SQLModel + SQLAlchemy）"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from loguru import logger

from app.config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
_engine_kwargs = dict(
    pool_pre_ping=True,
    echo=settings.debug,
    connect_args=_connect_args,
)
if not settings.database_url.startswith("sqlite"):
    _engine_kwargs.update(pool_size=10, max_overflow=20)
engine = create_engine(settings.database_url, **_engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库表（含大数据分析模块模拟数据集）"""
    # 确保所有 SQLModel 表类被导入到 metadata
    from app.models.house import (
        HouseInfo, HouseComment, HouseCommentReply, HouseRecommend, HouseCompare, BrowseHistory,
    )
    from app.models.user import UserInfo, Admin
    from app.models.trend import HousePriceTrend
    # RBAC0 新表：只新增 rbac_* 表，不改动已有表结构
    from app.models.rbac import Role, Permission, RolePermission  # noqa: F401
    # 知识库文档元信息表：只新增 knowledge_document，不改动已有表结构
    from app.models.knowledge import KnowledgeDocument  # noqa: F401
    # 站内消息表：只新增 user_notification，不改动已有表结构
    from app.models.notification import UserNotification  # noqa: F401

    logger.info("Initializing database tables with SQLModel...")
    SQLModel.metadata.create_all(bind=engine)
    logger.info("Database tables initialized.")

    # 大数据分析模块模拟数据集（用户 / 交互 / 评论情感 / 房价时序）
    db = SessionLocal()
    try:
        # RBAC 角色 / 权限初始化（幂等，服务启动即有权限数据，无需手工 SQL）
        from app.services.rbac_service import seed_rbac
        seed_rbac(db)

        from app.services.seed_simulated_data import seed_all_simulated
        seed_all_simulated(db)
    finally:
        db.close()
