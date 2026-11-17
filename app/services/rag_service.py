# -*- coding: utf-8 -*-
"""RAG 核心服务（LangChain + LangGraph 实现）

职责划分：
    app.services.rag_graph.RAGGraph   -> 负责「意图路由 -> 向量检索 -> 生成」的编排
    app.services.langchain_adapters   -> 负责把向量库/Embedding 接入 LangChain
    app.services.llm_service          -> 负责 ChatOpenAI 调用与 Prompt 模板
    本文件                            -> 保留原有对外接口，负责查库补全、会话历史、API 响应组装

对外接口与改造前完全一致：chat / chat_stream / index_houses / index_document /
get_conversation_history，因此前端与 API 路由无需任何改动。
"""

import time
import uuid
from typing import AsyncGenerator, Dict, List, Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger
from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import RAGException
from app.models.house import HouseInfo
from app.services.house_service import HouseService
from app.services.langchain_adapters import get_doc_store, get_house_store
from app.services.external_geo_service import ExternalGeoService, get_external_geo_service
from app.services.llm_service import get_llm_service
from app.services.rag_graph import get_rag_graph
from app.services.vector_store import get_vector_store


# 房源库只覆盖北京。用户问其它城市时，向量检索仍会（语义上）召回北京房源，
# 模型很容易把「北京」改写成「深圳」编出一份答案 —— 所以在入库前先做确定性拦截。
_OTHER_CITIES = (
    "深圳", "上海", "广州", "杭州", "成都", "重庆", "武汉", "南京", "天津", "西安",
    "苏州", "长沙", "郑州", "青岛", "厦门", "合肥", "济南", "沈阳", "大连", "昆明",
    "宁波", "无锡", "佛山", "东莞", "珠海", "三亚", "海口", "石家庄", "太原", "南昌",
    "贵阳", "南宁", "兰州", "哈尔滨", "长春", "福州", "乌鲁木齐",
)


class RAGService:
    def __init__(self, db: Optional[Session] = None):
        self.top_k = settings.rag_top_k
        self.score_threshold = settings.rag_score_threshold
        self.db = db
        self._conversations: Dict[str, List[dict]] = {}

    # ---------- 内部工具 ----------

    def _load_related_houses(self, house_metas: List[dict]) -> List[HouseInfo]:
        """根据检索命中的 metadata 回库取完整房源对象（按顺序去重）"""
        if not house_metas or not self.db:
            return []

        house_service = HouseService(self.db)
        related_houses: List[HouseInfo] = []
        seen = set()
        for meta in house_metas:
            hid = meta.get("house_id")
            if not hid or hid in seen:
                continue
            try:
                house = house_service.get_house_by_id(int(hid))
            except (ValueError, TypeError):
                continue
            if house:
                related_houses.append(house)
                seen.add(hid)
        return related_houses

    def _save_message(self, conversation_id: str, role: str, content: str):
        if conversation_id not in self._conversations:
            self._conversations[conversation_id] = []
        self._conversations[conversation_id].append({"role": role, "content": content})

    @staticmethod
    def _out_of_scope_city(question: str) -> Optional[str]:
        """问题里出现北京以外的城市 → 返回城市名（直接告知未收录，不做检索）。

        判据很朴素：出现"北京"就不拦（可能是对比类问题，交给模型按上下文回答），
        否则命中城市词表就拦。宁可少拦，也不要用北京房源冒充外地房源。
        """
        q = question or ""
        if "北京" in q:
            return None
        for city in _OTHER_CITIES:
            if city in q:
                return city
        return None

    @staticmethod
    def _coverage_prompt(city: str, question: str, areas: Optional[List[dict]] = None):
        """越界城市：不是硬拒绝，而是「说清边界 + 继续给通用建议」。

        库里没有该城市数据 → 禁止编造具体小区/价格；
        但购房资格、首付月供测算、看房要核实什么，这些常识依然可以答。
        """
        system = (
            f"你是安居乐房产智能助手。系统房源库只覆盖北京，没有收录{city}的房源数据。\n"
            "回答要求：\n"
            f"1. 开头就明确告知未收录{city}的房源，不要编造{city}的小区名、价格、面积\n"
            "2. 不要只拒绝：给出可用的通用建议（购房资格与首付/月供的一般规律、如何由预算反推可承受总价、"
            "看房应核实产权年限/满五唯一/学区/通勤）\n"
            "3. 结尾给出一个可以继续问的方向（例如同预算的北京片区对比）\n"
            "4. 中文，分点，300 字以内"
        )
        if areas:
            # 高德补的真实地理点：能提，但不能当小区、不能带价格
            system += (
                "\n\n" + ExternalGeoService.format_for_prompt(areas) +
                "\n可以据此说明该城市的真实片区/地铁分布，但必须遵守：\n"
                "- 这些是商圈/地铁站，不是小区名，禁止说成「某小区」「某楼盘」\n"
                "- 禁止给出任何价格、面积、户型、总价区间\n"
                "- 片区只做一句概括，重点仍放在通用建议上"
            )
        return system, f"用户问题：{question}"

    @staticmethod
    def _coverage_message(city: str) -> str:
        return (
            f"目前系统的房源库只覆盖北京，没有收录{city}的房源，"
            f"所以给不出{city}的小区、报价和户型数据（编一个价格对您没有意义）。\n\n"
            "可以改成问北京的片区，例如：\n"
            "• 朝阳区 500 万以内的两居\n"
            "• 海淀区带学区的大三居\n"
            "• 通州区近地铁的一居"
        )

    # ---------- 非流式问答 ----------

    async def chat(self, question, conversation_id=None, user_id=None, top_k=None):
        start_time = time.time()
        conversation_id = conversation_id or uuid.uuid4().hex

        blocked_city = self._out_of_scope_city(question)
        if blocked_city:
            geo = get_external_geo_service()
            district = geo.match_district(blocked_city, question) if geo.enabled else None
            areas = geo.business_areas(blocked_city, district) if geo.enabled else []
            try:
                system, prompt = self._coverage_prompt(blocked_city, question, areas)
                text = await get_llm_service().generate(prompt, system)
            except Exception:
                text = self._coverage_message(blocked_city)
            self._save_message(conversation_id, "user", question)
            self._save_message(conversation_id, "assistant", text)
            return {
                "answer": text,
                "conversation_id": conversation_id,
                "sources": [],
                "model": settings.llm_model_name,
                "latency": round(time.time() - start_time, 3),
                "related_houses": [],
                "external_pois": areas,
            }

        final_state = await get_rag_graph().run(question, conversation_id)

        answer = final_state.get("answer", "") or ""
        sources = final_state.get("sources") or []
        related_houses = self._load_related_houses(final_state.get("house_metas") or [])

        latency = time.time() - start_time
        self._save_message(conversation_id, "user", question)
        self._save_message(conversation_id, "assistant", answer)

        return {
            "answer": answer,
            "conversation_id": conversation_id,
            "sources": sources,
            "model": settings.llm_model_name,
            "latency": round(latency, 3),
            "related_houses": [h.to_dict() for h in related_houses],
        }

    # ---------- 流式问答（SSE） ----------

    async def chat_stream(self, question, conversation_id=None, user_id=None, top_k=None):
        import json

        start_time = time.time()
        conversation_id = conversation_id or uuid.uuid4().hex

        answer = ""
        sources: List[dict] = []

        house_metas: List[dict] = []
        start_sent = False

        def _sse(payload: dict) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        def _start_event(srcs: List[dict]) -> str:
            return _sse({
                "type": "start",
                "conversation_id": conversation_id,
                "sources": srcs,
            })

        # 北京以外的城市：不检索（检索只会召回北京房源），但也不硬拒绝 ——
        # 用通用 Prompt 让模型说清边界、继续给常识建议
        blocked_city = self._out_of_scope_city(question)
        if blocked_city:
            text = ""
            # 外部地理补全：区名 → 该区商圈/地铁（失败返回空，不影响回答）
            geo = get_external_geo_service()
            district = geo.match_district(blocked_city, question) if geo.enabled else None
            areas = geo.business_areas(blocked_city, district) if geo.enabled else []
            yield _start_event([])
            try:
                system, prompt = self._coverage_prompt(blocked_city, question, areas)
                async for chunk in get_llm_service().generate_stream(prompt, system):
                    text += chunk
                    yield _sse({"type": "delta", "content": chunk})
            except Exception as e:
                logger.warning(f"[RAG] 越界城市回答生成失败，回退固定文案: {e}")
                text = text or self._coverage_message(blocked_city)
                for i in range(0, len(text), 24):
                    yield _sse({"type": "delta", "content": text[i:i + 24]})
            self._save_message(conversation_id, "user", question)
            self._save_message(conversation_id, "assistant", text)
            yield _sse({
                "type": "done",
                "latency": round(time.time() - start_time, 3),
                "model": settings.llm_model_name,
                "related_houses": [],
                # 外部地理点：前端用灰色标记画在对话地图上，明确区分「不是房源」
                "external_pois": areas,
            })
            return

        try:
            async for kind, payload in get_rag_graph().astream(question, conversation_id):
                if kind == "event":
                    event_type = payload.get("type")
                    if event_type == "retrieve_done":
                        sources = payload.get("sources") or []
                        # 检索完成即刻把来源发给前端（此时还没开始吐字）
                        if sources and not start_sent:
                            start_sent = True
                            yield _start_event(sources)
                    elif event_type == "agent_step":
                        # 多智能体路由可视化：透传给前端，由前端展示「智能体调度：xx」
                        yield _sse(payload)
                    elif event_type == "delta":
                        text = payload.get("content", "")
                        if text:
                            if not start_sent:
                                start_sent = True
                                yield _start_event(sources)
                            answer += text
                            yield _sse({"type": "delta", "content": text})
                elif kind == "state":
                    house_metas = payload.get("house_metas") or house_metas
                    sources = payload.get("sources") or sources
                    answer = payload.get("answer") or answer
        except Exception as e:
            logger.warning(f"[RAG] stream failed: {e}")
            answer = answer or f"抱歉，回答生成出现问题：{e}"
            if not start_sent:
                start_sent = True
                yield _start_event(sources)
            yield _sse({"type": "delta", "content": answer})

        if not start_sent:
            start_sent = True
            yield _start_event(sources)

        related_houses = self._load_related_houses(house_metas)
        latency = time.time() - start_time
        self._save_message(conversation_id, "user", question)
        self._save_message(conversation_id, "assistant", answer)

        yield _sse({
            "type": "done",
            "answer": answer,
            "conversation_id": conversation_id,
            "latency": round(latency, 3),
            "model": settings.llm_model_name,
            "related_houses": [h.to_dict() for h in related_houses],
        })

    # ---------- 索引写入（走 LangChain VectorStore 接口） ----------

    # Chroma 底层是 SQLite，单次写入的变量数有上限（实测 5461 条就报
    # "Batch size of 16000 is greater than max batch size of 5461"）。
    # 分批写是必须的：只要索引量超过 5000 条，一次性 add 必然失败。
    _INDEX_BATCH = 1000

    def index_houses(self, houses: List[HouseInfo]) -> int:
        if not houses:
            return 0

        store = get_house_store()
        indexed = 0
        for start in range(0, len(houses), self._INDEX_BATCH):
            batch = houses[start:start + self._INDEX_BATCH]
            docs = [house.to_search_doc() for house in batch]
            metadatas = [
                {
                    "house_id": house.id, "title": house.title or "",
                    "address": house.address or "", "price": house.price or "",
                    "rooms": house.rooms or "", "area": house.area or "",
                    "region": house.region or "",
                }
                for house in batch
            ]
            store.add_texts(docs, metadatas=metadatas)
            indexed += len(batch)

        logger.info(f"Indexed {indexed} houses into vector store（分批 {self._INDEX_BATCH} 条）")
        return indexed

    def reindex_houses(self, limit: int = None, keep_old: bool = False) -> dict:
        """按「小区 × 户型」分层抽样，重建房源向量索引

        修复过的三个问题：
            1. 旧逻辑用 `db.query(HouseInfo).limit(N)` 按 id 顺序取前 N 条，
               实测前 1000 条全部属于朝阳区，其余 11 个区域一条都没有；
            2. 旧逻辑是追加写入，重复调用会让同一批房源在向量库里堆积多份；
            3. 改成"每区取前 N 条"之后仍有偏差：房源按街道成团落库，某个小区
               可能整片落在配额之外——limit 从 1000 调到 4000 时，安苑里 /
               双龙南里 / 安慧里三区 直接从向量库里消失，搜小区名搜不到。

        写入策略：先写新向量，成功后再删旧向量，避免中途失败导致向量库为空。
        """
        if not self.db:
            raise RAGException("重建索引需要数据库会话")

        limit = limit or settings.rag_house_index_limit

        from sqlalchemy import func

        # 1. 统计区域分布
        region_rows = (
            self.db.query(HouseInfo.region, func.count(HouseInfo.id))
            .group_by(HouseInfo.region)
            .all()
        )
        total = sum(count for _, count in region_rows)
        if total == 0:
            raise RAGException("数据库中没有房源数据")

        # 2. 分层抽样：先保证「小区 × 户型」组合各 1 条，余量按 id 顺序补
        #
        #    为什么不能按区域取前 N 条：
        #        房源是按街道成团落库的，某个小区可能整片落在配额之外。把 limit 从
        #        1000 调到 4000 后，安苑里 / 双龙南里 / 安慧里三区 这些小社区直接从
        #        向量库里消失了——用户搜小区名搜不到，评测里那几道题也集体失效，
        #        而分数看起来只是"检索变差了"，很难想到是抽样偏了。
        #    为什么按「小区 × 户型」而不是按小区：
        #        评测和线上都关心"某小区有没有两居"，只按小区保底仍可能漏掉某个户型。
        #        按组合保底之后，扩量/缩量都不会让历史评测集失效。
        combo_min = (
            self.db.query(
                HouseInfo.address, HouseInfo.rooms, func.min(HouseInfo.id)
            )
            .group_by(HouseInfo.address, HouseInfo.rooms)
            .all()
        )
        selected_ids = {min_id for _addr, _rooms, min_id in combo_min if min_id}
        combos = len(selected_ids)

        # 余量：按 id 顺序扫描补齐（幂等、结果稳定，重跑得到同一批数据）
        if len(selected_ids) < limit:
            for (hid,) in self.db.query(HouseInfo.id).order_by(HouseInfo.id).yield_per(2000):
                if len(selected_ids) >= limit:
                    break
                selected_ids.add(hid)

        houses: List[HouseInfo] = []
        if selected_ids:
            ids = sorted(selected_ids)
            for start in range(0, len(ids), 1000):
                chunk = ids[start:start + 1000]
                houses.extend(
                    self.db.query(HouseInfo).filter(HouseInfo.id.in_(chunk)).all()
                )

        # 3. 记录旧向量（保留 id 以便写入成功后清理）
        store = get_vector_store()
        old_ids, old_metadatas = store.get_entries("house")
        old_rent = sum(
            1 for meta in old_metadatas if "元/月" in str((meta or {}).get("price", ""))
        )

        # 4. 写入新向量
        indexed = self.index_houses(houses) if houses else 0

        # 5. 写入成功后删除旧向量
        deleted = 0
        if indexed and old_ids and not keep_old:
            deleted = store.delete_ids(old_ids)

        logger.info(
            f"Reindex houses: db={total}, sampled={len(houses)}, indexed={indexed}, "
            f"old={len(old_ids)}, deleted={deleted}"
        )
        return {
            "total_in_db": total,
            "region_count": len(region_rows),
            "per_region": combos,
            "sampled": len(houses),
            "indexed": indexed,
            "old_vectors": len(old_ids),
            "old_rent_vectors": old_rent,
            "deleted": deleted,
            "house_vectors": store.count("house"),
        }

    async def index_document(self, content, document_name, doc_id=None):
        doc_id = doc_id or uuid.uuid4().hex
        chunks = self._split_text(content)
        if not chunks:
            raise RAGException("文档内容为空")

        metadatas = [{"document_name": document_name, "chunk_index": i} for i in range(len(chunks))]
        get_doc_store().add_texts(chunks, metadatas=metadatas, doc_id=doc_id)

        logger.info(f"Indexed document: {document_name}, chunks: {len(chunks)}")
        return {"doc_id": doc_id, "chunk_count": len(chunks), "status": "completed"}

    def _split_text(self, text: str) -> List[str]:
        """文档切分改用 LangChain RecursiveCharacterTextSplitter（中文友好分隔符）"""
        if not text or not text.strip():
            return []
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.rag_chunk_size,
            chunk_overlap=settings.rag_chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        )
        return splitter.split_text(text)

    # ---------- 会话历史 ----------

    def get_conversation_history(self, conversation_id: str) -> List[dict]:
        return self._conversations.get(conversation_id, [])
