#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目启动脚本"""

import os
from dotenv import load_dotenv

load_dotenv()

# Ensure data directories exist
os.makedirs("./data/vector_db", exist_ok=True)
os.makedirs("./data/documents", exist_ok=True)


def init_database():
    """初始化数据库"""
    try:
        from app.database import init_db
        init_db()
        print("[OK] Database initialized successfully.")
    except Exception as e:
        print(f"[WARN] Database init failed: {e}")


if __name__ == "__main__":
    import uvicorn
    init_database()
    # 端口/监听地址可由 .env 覆盖，未配置时与 README 一致（8001）
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8001"))
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
