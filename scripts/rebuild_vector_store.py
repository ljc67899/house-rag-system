# -*- coding: utf-8 -*-
"""重建房源向量库

用途：数据库中的房源数据被整体替换后，向量库可能仍保留旧数据
（例如从租房数据换成二手房数据）。本脚本按区域均衡抽样当前房源，
重新建立向量索引，并在成功后删除旧向量。

与实际接口 POST /api/knowledge/index-houses 共用同一套逻辑
（RAGService.reindex_houses），避免两处实现不一致。

用法（在项目根目录执行）：
    python scripts/rebuild_vector_store.py            # 默认抽样 1000 条
    python scripts/rebuild_vector_store.py --limit 2000
"""

import argparse
import sys
from pathlib import Path

# 保证从任意位置执行都能 import 到 app 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.services.index_sync import IndexSyncService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="重建房源向量库")
    parser.add_argument("--limit", type=int, default=0,
                        help="抽样总条数（按区域平均分配）；不传则取 settings.rag_house_index_limit")
    parser.add_argument("--keep-old", action="store_true", help="保留旧向量（排错用）")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        service = IndexSyncService(db)
        result = service.rebuild(limit=args.limit or None, keep_old=args.keep_old)

        print(f"[数据库] 共 {result['total_in_db']} 条房源，覆盖 {result['region_count']} 个区域")
        print(f"[旧向量] {result['old_vectors']} 条，其中价格含「元/月」(租房) {result['old_rent_vectors']} 条")
        print(f"[抽样]   小区×户型组合 {result['per_region']} 个，合计 {result['sampled']} 条")
        print(f"[索引]   已写入 {result['indexed']} 条向量")
        print(f"[清理]   已删除旧向量 {result['deleted']} 条")
        print(f"[完成]   当前 house 向量总数: {result['house_vectors']}")
        return 0 if result["indexed"] else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
