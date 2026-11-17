# -*- coding: utf-8 -*-
"""按房源补齐模拟评论（幂等）。

历史模拟评论是随机撒点，38267 套房源里只有 1000 多套有评论，
详情页因此几乎只能看到自己刚发的那一条。运行本脚本后每套房源
至少有 3 条来自其他用户的评价。

用法：python scripts/backfill_house_comments.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.services.seed_simulated_data import ensure_house_comment_coverage  # noqa: E402


def main():
    db = SessionLocal()
    try:
        added = ensure_house_comment_coverage(db)
        logger.info(f"done, added {added} comments")
    finally:
        db.close()


if __name__ == "__main__":
    main()
