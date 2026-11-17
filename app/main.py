# -*- coding: utf-8 -*-
"""FastAPI 应用入口 - 房产智能问答推荐系统"""

import secrets
import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from loguru import logger
from app.config import settings
from app.api import api_router
from app.models import schemas

# 静态资源目录（绝对路径，避免依赖启动时的工作目录）
STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"
# 独立后台：与前台完全分离的一套页面（侧边栏布局 + 自己的 JS/CSS）
ADMIN_FILE = STATIC_DIR / "admin" / "index.html"


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="基于 RAG 技术的房产智能问答推荐系统 API",
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(api_router, prefix="/api")

    @app.get("/", include_in_schema=False)
    async def index():
        """系统首页：直接返回前端页面，无需带 /static/index.html"""
        return FileResponse(INDEX_FILE)

    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/", include_in_schema=False)
    async def admin_index():
        """管理后台：独立页面，与前台共用 /api 但不共用前端代码"""
        if not ADMIN_FILE.exists():
            return FileResponse(INDEX_FILE)
        return FileResponse(ADMIN_FILE)

    @app.get("/health", response_model=schemas.HealthResponse, tags=["基础"])
    async def health_check():
        db_ok = False
        try:
            from app.database import engine
            with engine.connect():
                db_ok = True
        except Exception:
            pass
        from app.services import get_vector_store
        vector_db_ok = get_vector_store().health_check()
        llm_ok = bool(settings.llm_api_key)
        return schemas.HealthResponse(
            status="healthy", version=settings.app_version,
            database_connected=db_ok, llm_connected=llm_ok,
            vector_db_connected=vector_db_ok,
        )

    @app.get("/api/config/map", tags=["基础"])
    async def map_config():
        """下发前端地图 SDK 需要的 Key

        为什么绕一层：这两个值原来是直接写死在 index.html / admin.js 里的，
        源码一旦公开，Key 就永久暴露在仓库历史里。改成后端下发后，仓库里不再
        出现密钥，换 Key 只要改 .env 重启，不用翻前端代码。
        注意：浏览器端仍然能看到这个值（JS API 的固有特性），真正的防线是
        在高德控制台给 Key 绑域名白名单 + 设置每日调用上限。
        """
        return {
            "key": settings.amap_js_key,
            "security_code": settings.amap_js_security_code,
        }

    @app.on_event("startup")
    async def startup_event():
        logger.info(f"Starting {settings.app_name} v{settings.app_version}")
        logger.info(f"Debug mode: {settings.debug}")
        # 首次启动时创建默认管理员账号（Admin 表为空时）
        try:
            from app.core.security import hash_password
            from app.database import SessionLocal
            from app.models.user import Admin
            db = SessionLocal()
            try:
                if db.query(Admin).count() == 0:
                    # 初始口令不能写死在代码里（仓库公开等于公开后台口令）。
                    # 优先取环境变量 ADMIN_INIT_PASSWORD；没有就随机生成并打印到日志。
                    pwd = (settings.admin_init_password or "").strip()
                    generated = False
                    if not pwd:
                        pwd = secrets.token_urlsafe(12)
                        generated = True
                    db.add(Admin(username="admin", password_hash=hash_password("admin", pwd)))
                    db.commit()
                    if generated:
                        logger.warning(
                            f"已创建管理员账号 admin，初始口令（仅本次打印，请立即保存并修改）: {pwd}"
                        )
                    else:
                        logger.warning(
                            "已创建管理员账号 admin，口令取自 ADMIN_INIT_PASSWORD，请登录后立即修改"
                        )
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"管理员账号初始化失败: {e}")

        # 启动时校验向量库与房源数据是否一致，不一致则自动重建
        # （房源数据被整体替换后，旧向量会导致 AI 答非所问）
        try:
            from app.database import SessionLocal
            from app.services.index_sync import IndexSyncService
            db = SessionLocal()
            try:
                info = IndexSyncService(db).ensure_fresh()
                if info.get("rebuilt"):
                    logger.info(
                        f"向量库已自动重建: {info.get('indexed')} 条, "
                        f"version={info.get('data_version')}"
                    )
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Vector index freshness check failed: {e}")

        # BM25 稀疏索引是懒建的，全量 38266 条建一次约 23s（含全库扫文本 10s）。
        # 如果等第一个用户查询来触发，首问会白卡半分钟——看起来像"服务卡死"。
        # 所以启动时丢到后台线程预热：不阻塞端口监听，失败了也只是退回单路稠密。
        def _warm_bm25():
            try:
                from app.services.sparse_index import get_house_bm25

                t0 = time.time()
                bm25 = get_house_bm25()
                if bm25:
                    logger.info(
                        f"[预热] BM25 稀疏索引就绪: {bm25.n} 条, {time.time() - t0:.1f}s"
                    )
                else:
                    logger.warning("[预热] BM25 未就绪，检索将退回单路稠密")
            except Exception as exc:
                logger.warning(f"[预热] BM25 建索引失败，检索将退回单路稠密: {exc}")

        try:
            threading.Thread(target=_warm_bm25, name="warm-bm25", daemon=True).start()
        except Exception as e:
            logger.warning(f"BM25 预热线程启动失败: {e}")

    @app.on_event("shutdown")
    async def shutdown_event():
        logger.info(f"Shutting down {settings.app_name}")

    return app


app = create_app()
