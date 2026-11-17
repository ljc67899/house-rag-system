# -*- coding: utf-8 -*-
"""修复 house_info 表 region / block 字段错位（幂等，默认 dry-run）。

问题：38267 条房源里有 7568 条 region 被写成了城市名“北京”，
真实行政区落在 block 字段里（如 block=丰台区）。这导致这些房源
按任何行政区筛选、统计、房价估算、RAG 检索都查不到。

本脚本只处理“block 恰好是北京行政区名”的记录：
    region = block        （block 保留不动，前端用 Set 去重展示，不会重复）

region 与 block 都是行政区但互不相同的记录（例如
region=石景山区 / block=海淀区 / address=建西苑中里）属于矛盾数据，
数量极少，脚本只列出、不修改，交由人工确认。

用法：
    python scripts/fix_region_block.py              # 只统计，不写库
    python scripts/fix_region_block.py --apply      # 备份后执行更新
    python scripts/fix_region_block.py --apply --backup-suffix=_bak1
"""

import argparse
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger  # noqa: E402
from sqlalchemy import text  # noqa: E402

# settings.debug 会打开 SQL 回显，脚本里只看统计结果，关掉噪声
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

from app.database import engine, SessionLocal  # noqa: E402

# 北京 16 个市辖区（行政区县取值白名单）
DISTRICTS = (
    "东城区", "西城区", "朝阳区", "丰台区", "石景山区", "海淀区", "门头沟区",
    "房山区", "通州区", "顺义区", "昌平区", "大兴区", "怀柔区", "平谷区",
    "密云区", "延庆区",
)
# 只写到城市级别的脏值，需要按 block 回填
CITY_LEVEL = ("北京", "北京市")

TABLE = "house_info"


def _rows(sql: str) -> list:
    with engine.connect() as conn:
        return list(conn.execute(text(sql)))


def _district_set() -> str:
    return ",".join(f"'{d}'" for d in DISTRICTS)


def survey(db) -> dict:
    """统计现状，不修改任何数据。"""
    total = db.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar() or 0

    region_dist = _rows(
        f"SELECT region, COUNT(*) AS cnt FROM {TABLE} "
        f"GROUP BY region ORDER BY cnt DESC"
    )
    logger.info(f"{TABLE} 共 {total} 条，region 取值 {len(region_dist)} 种：")
    for name, cnt in region_dist:
        flag = "" if name in DISTRICTS else "   <-- 非行政区"
        logger.info(f"    {name!r:<12} {cnt:>7}{flag}")

    # 1) 城市级脏值：block 是否可信
    city_clause = " OR ".join(f"region = '{c}'" for c in CITY_LEVEL)
    city_total = db.execute(
        text(f"SELECT COUNT(*) FROM {TABLE} WHERE {city_clause}")
    ).scalar() or 0

    block_dist = _rows(
        f"SELECT block, COUNT(*) AS cnt FROM {TABLE} "
        f"WHERE {city_clause} GROUP BY block ORDER BY cnt DESC"
    )
    fixable = sum(cnt for name, cnt in block_dist if name in DISTRICTS)
    unfixable = city_total - fixable

    logger.info(f"\nregion 为城市级（{CITY_LEVEL}）的记录：{city_total} 条")
    logger.info(f"    其中 block 是合法行政区、可自动回填：{fixable} 条")
    logger.info(f"    其余无法自动判断（保持原样）：{unfixable} 条")
    for name, cnt in block_dist:
        mark = "可回填" if name in DISTRICTS else "跳过"
        logger.info(f"    block={name!r:<12} {cnt:>7}  [{mark}]")

    # 2) region / block 都是行政区但互相矛盾：只报告
    conflicts = _rows(
        f"SELECT id, region, block, address FROM {TABLE} "
        f"WHERE region IN ({_district_set()}) AND block IN ({_district_set()}) "
        f"AND region <> block"
    )
    if conflicts:
        logger.warning(f"\nregion 与 block 均为行政区但互不相同：{len(conflicts)} 条（请人工确认，脚本不处理）")
        pairs = Counter((r, b) for _, r, b, _ in conflicts)
        logger.warning("    按 (region, block) 组合统计：")
        for (r, b), cnt in pairs.most_common(20):
            logger.warning(f"      {r} / {b}: {cnt}")
        logger.warning("    样例：")
        for hid, region, block, address in conflicts[:10]:
            logger.warning(f"      id={hid} region={region} block={block} address={address}")

    return {
        "total": total,
        "city_total": city_total,
        "fixable": fixable,
        "unfixable": unfixable,
        "conflicts": len(conflicts),
        "region_kinds": len(region_dist),
    }


def apply(db, backup_suffix: str) -> int:
    """备份表后执行回填，返回更新行数。"""
    backup_table = f"{TABLE}{backup_suffix}"
    with engine.begin() as conn:
        exists = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = :t"
        ), {"t": backup_table}).scalar()
        if exists:
            logger.info(f"备份表 {backup_table} 已存在，跳过备份")
        else:
            logger.info(f"创建备份表 {backup_table} ...")
            conn.execute(text(f"CREATE TABLE {backup_table} AS SELECT * FROM {TABLE}"))
            logger.info("备份完成")

    sql = (
        f"UPDATE {TABLE} SET region = block "
        f"WHERE region IN ({','.join(repr(c) for c in CITY_LEVEL)}) "
        f"AND block IN ({_district_set()})"
    )
    with engine.begin() as conn:
        result = conn.execute(text(sql))
        updated = result.rowcount or 0
    logger.info(f"已更新 {updated} 条记录：region = block")
    return updated


def main():
    parser = argparse.ArgumentParser(description="修复 house_info.region/block 字段错位")
    parser.add_argument("--apply", action="store_true", help="实际写库（默认只统计）")
    parser.add_argument(
        "--backup-suffix",
        default=f"_bak_{datetime.now().strftime('%Y%m%d')}",
        help="备份表后缀，默认 _bak_YYYYMMDD",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        logger.info("=" * 60)
        logger.info("修复前现状")
        logger.info("=" * 60)
        before = survey(db)

        if not args.apply:
            logger.info("\n当前为 dry-run，未修改任何数据。确认无误后加 --apply 执行。")
            return

        if before["fixable"] == 0:
            logger.info("\n没有可回填的记录，无需执行。")
            return

        logger.info("\n" + "=" * 60)
        logger.info("开始执行回填")
        logger.info("=" * 60)
        apply(db, args.backup_suffix)

        logger.info("\n" + "=" * 60)
        logger.info("修复后复查")
        logger.info("=" * 60)
        survey(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
