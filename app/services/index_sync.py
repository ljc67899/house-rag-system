# -*- coding: utf-8 -*-
"""向量索引同步服务

要解决的问题：
    向量库是「派生数据」。数据库里的房源一旦被整体替换（例如从租房数据换成二手房数据），
    向量库不同步就会答非所问。旧实现启动时只判断「库里有没有数据」，不判断「数据对不对」，
    所以发现不了这类不一致。

本服务提供三件事：

    1. 数据版本号
       用「房源总数 + 最大 id + 最新 updated_at」刻画当前数据版本，
       重建索引时把版本号写进 ChromaDB collection 的 metadata。

    2. 启动校验
       启动时比对「库内版本」与「数据库版本」，不一致则自动重建。

    3. 单条同步
       后台新增 / 修改 / 删除房源后，立即同步该条房源对应的向量，
       避免运行期间出现「页面改了、AI 还是旧答案」。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.house import HouseInfo
from app.services.vector_store import get_vector_store

# 版本号文件（与向量库放在一起）
# 说明：ChromaDB 1.x 不支持 modify collection metadata（会触发距离函数校验失败），
#      因此把版本号单独落一个 JSON 文件，同样能实现「库-数据」一致性校验。
VERSION_FILE = Path(settings.vector_db_path) / "index_version.json"
VERSION_KEY = "house_data_version"


class IndexSyncService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 数据版本号 ----------

    def compute_data_version(self) -> str:
        """房源数据版本 = 总条数 - 最大id - 最新更新时间

        三个维度组合可以覆盖全部变更：
            新增 -> 条数与最大 id 变化
            修改 -> 最新 updated_at 变化
            删除 -> 条数变化
        """
        count, max_id, max_updated = self.db.query(
            func.count(HouseInfo.id),
            func.max(HouseInfo.id),
            func.max(HouseInfo.updated_at),
        ).one()
        stamp = max_updated.strftime("%Y%m%d%H%M%S") if max_updated else "none"
        return f"{count}-{max_id or 0}-{stamp}"

    def get_index_info(self) -> dict:
        """读取索引版本信息（不存在则返回空字典）"""
        try:
            if VERSION_FILE.exists():
                return json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取索引版本文件失败: {e}")
        return {}

    def get_stored_version(self) -> Optional[str]:
        """读取向量库中记录的数据版本"""
        return self.get_index_info().get(VERSION_KEY)

    def save_version(self, version: Optional[str] = None) -> str:
        """把数据版本写入版本文件"""
        version = version or self.compute_data_version()
        payload = {
            VERSION_KEY: version,
            "house_vectors": get_vector_store().count("house"),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            VERSION_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"向量库数据版本已写入: {version} -> {VERSION_FILE}")
        except Exception as e:
            logger.error(f"写入索引版本文件失败: {e}")
        return version

    def is_stale(self) -> bool:
        return self.get_stored_version() != self.compute_data_version()

    # ---------- 全量重建 ----------

    def rebuild(self, limit: int = None, keep_old: bool = False) -> dict:
        """按区域均衡抽样重建房源向量，并写入数据版本号"""
        from app.services.rag_service import RAGService

        limit = limit or settings.rag_house_index_limit
        result = RAGService(self.db).reindex_houses(limit=limit, keep_old=keep_old)
        result["data_version"] = self.save_version()
        return result

    # ---------- 启动校验 ----------

    def ensure_fresh(self, limit: int = None, auto_rebuild: bool = True) -> dict:
        """启动时校验向量库是否与数据库一致，不一致则重建"""
        # 这里的默认值必须和手动重建保持一致，否则启动时一自动重建就把
        # 扩到 4000 条的索引又缩回 1000 条，扩量等于白做。
        limit = limit or settings.rag_house_index_limit
        stored = self.get_stored_version()
        current = self.compute_data_version()
        house_vectors = get_vector_store().count("house")

        info = {
            "stored_version": stored,
            "current_version": current,
            "house_vectors_before": house_vectors,
            "stale": stored != current,
            "rebuilt": False,
        }

        if stored == current and house_vectors > 0:
            logger.info(f"向量库与房源数据一致 (version={current}, vectors={house_vectors})")
            return info

        if house_vectors == 0:
            logger.warning("向量库为空，开始建立索引...")
        elif stored is None:
            logger.warning(f"向量库缺少数据版本号，按当前数据版本重建 (current={current})")
        else:
            logger.warning(f"向量库数据已过期: 库内版本={stored} | 数据库版本={current}")

        if not auto_rebuild:
            return info

        try:
            result = self.rebuild(limit=limit)
            info.update({
                "rebuilt": True,
                "indexed": result.get("indexed"),
                "house_vectors": result.get("house_vectors"),
                "data_version": result.get("data_version"),
            })
            logger.info(f"向量库自动重建完成: {result.get('indexed')} 条向量")
        except Exception as e:
            logger.error(f"向量库自动重建失败: {e}")
            info["error"] = str(e)

        return info

    # ---------- 单条房源同步 ----------

    def remove_house_vectors(self, house_id: int) -> int:
        """删除某条房源对应的所有向量"""
        store = get_vector_store()
        ids = store.get_ids_where({"house_id": int(house_id)})
        return store.delete_ids(ids)

    def sync_house(self, house: HouseInfo) -> bool:
        """新增 / 修改房源后同步单条向量：先删旧向量，再写入新向量"""
        from app.services.rag_service import RAGService

        if not house or house.id is None:
            return False
        try:
            self.remove_house_vectors(house.id)
            RAGService(self.db).index_houses([house])
            self.save_version()
            logger.info(f"房源 {house.id} 的向量已同步")
            return True
        except Exception as e:
            logger.error(f"房源 {house.id} 的向量同步失败: {e}")
            return False


def get_index_sync_service(db: Session) -> IndexSyncService:
    return IndexSyncService(db)


__all__ = ["IndexSyncService", "get_index_sync_service", "VERSION_KEY"]
