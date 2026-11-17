# -*- coding: utf-8 -*-
"""基于 LangGraph 的多智能体问答编排（Supervisor + 专家 Agent）

图结构：

                    ┌──(结构化条件)──> house_search ──────────────┐
    START ─> analyze_intent ─> supervisor ──(政策知识)──> rewrite_query ─> knowledge ─┤
                    │                    ├──(行情/预测)──> analytics ────────────────┤
                    │                    └──(混合/兜底)──> rewrite_query ─> retrieve ─┤
                    └──(寒暄闲聊)──────────────────────────────────────────────────> generate ─> END

三处和多 Agent 有关的设计：

1. **Supervisor 分级决策**
   先走规则（关键词 + 实体识别，零成本、可解释），规则给不出结论时才让 LLM 做一次分类。
   不给所有问题都加一次模型调用，避免"为了多 Agent 而多 Agent"。

2. **专家各持有自己的工具，互不共享上下文**
   - house_search：SQL 精确过滤（区域/预算/户型/面积），解决向量检索对硬条件不敏感的问题
   - knowledge：知识库向量检索（政策、税费、流程）
   - analytics：LSTM 走势预测 + 行情统计（单价分位、总价中位数）
   - retrieve：混合兜底——房源库做 稠密向量 + BM25 稀疏 的 RRF 融合，
     知识库做单路向量召回（两类语料候选不重叠，跨库排序没有意义）

3. **失败不互相拖累**
   任一专家异常只把自己那份结果置空，generate 仍然出字；路由阶段出错直接降级 hybrid。

对外接口（run / astream / 事件协议）与单 Agent 版本完全一致，前端无需改动。
"""

import json
import re
from typing import Annotated, Dict, List, Optional, Tuple, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from loguru import logger

from app.config import settings
from app.services.house_filters import as_rooms_list, eval_constraints
from app.services.langchain_adapters import get_doc_store, get_house_store
from app.services.reranker_service import get_reranker
from app.services.sparse_index import doc_key as _house_doc_key, get_house_bm25
from app.services.llm_service import (
    GENERAL_SYSTEM_PROMPT,
    RAG_SYSTEM_PROMPT,
    build_context,
    get_llm_service,
)
from app.utils.regions import BEIJING_DISTRICTS

# 寒暄 / 闲聊关键词：命中则不走向量检索，直接生成
CHITCHAT_KEYWORDS = (
    "你好", "您好", "hi", "hello", "在吗", "你是谁", "你能做什么",
    "谢谢", "感谢", "再见", "拜拜", "早上好", "晚上好", "你会什么",
)

# ---------- 路由关键词（Supervisor 规则层） ----------

# 政策 / 知识类：答案在文档里，不需要房源检索
KNOWLEDGE_WORDS = (
    "政策", "限购", "资格", "税费", "契税", "个税", "增值税", "贷款", "商贷", "公积金",
    "首付比例", "过户", "流程", "注意事项", "风险", "产权", "满五", "满二", "合同",
    "学区划分", "落户", "摇号", "公摊", "得房率", "维修基金",
)

# 行情 / 预测类：需要调走势模型与行情统计
ANALYTICS_WORDS = (
    "走势", "趋势", "预测", "会涨", "会跌", "涨还是跌", "涨幅", "跌幅", "降价", "抄底",
    "行情", "投资", "回报", "租金", "性价比", "值得买", "未来", "板块", "成交价", "单价多少",
    # 「西城区单价现在多少」这类问的是区域行情，不是某一套房源——早先只写了
    # 「单价多少」这个完整词，中间插个「现在」就漏掉了，被区域条件误判成找房
    "单价", "均价", "多少钱一平",
)

# 找房类：结构化条件（区域/预算/户型/面积）
HOUSE_WORDS = (
    "推荐", "找个", "找一套", "有没有", "房源", "两居", "三居", "一居", "四居",
    "室", "厅", "预算", "以内", "以下", "小区", "地铁", "首付能", "全款",
)

# 「找房条件 + 泛化诉求」并存：规则层只看硬条件就判 house_search 会漏掉行情/知识两路。
# 「南十里居这个小区怎么样」「帮我对比一下这几个小区」「现在适合出手吗」要的是
# 检索 + 行情 + 知识三合一，所以这类交给 LLM 判 hybrid，而不是硬派给 house_search。
VAGUE_WORDS = (
    "怎么样", "如何", "对比", "比较", "适合", "合适", "出手",
    "建议", "有限", "改善", "怎么选", "值不值",
)

# 口语化的条件表达：正则会漏，值得多调一次模型兜底（「预算不多」「一百来万」「小两居」）
# 按维度拆开判断——「预算不多想买两居」抽到了户型但没抽到价格，价格这一维仍然是空的，
# 不该因为「已经抽到一个条件」就放过。
_LOOSE_PRICE_WORDS = ("预算", "首付", "价位", "总价", "便宜", "贵", "来万", "出头")
_LOOSE_ROOMS_WORDS = ("小两居", "小三居", "大一居", "小户型", "开间", "几居")
_LOOSE_AREA_WORDS = ("多少平", "多大面积", "面积大")

# relaxed 里存的是内部维度标识，写给模型看时要换成中文（"price" -> "价格"）
_FILTER_DIM_CN = {"area": "面积", "price": "价格", "region": "区域"}

ROUTE_HOUSE = "house_search"
ROUTE_KNOWLEDGE = "knowledge"
ROUTE_ANALYTICS = "analytics"
ROUTE_HYBRID = "hybrid"

ROUTE_LABELS = {
    ROUTE_HOUSE: "房源检索",
    ROUTE_KNOWLEDGE: "政策知识",
    ROUTE_ANALYTICS: "行情分析",
    # 注意别写成「混合检索」：那是 hybrid retrieval（稀疏+稠密）的专有说法，
    # 本项目 hybrid 指的是「混合问题」，前端直接展示这个 label，措辞要准
    ROUTE_HYBRID: "双路召回",
}

# ---------- 结构化条件抽取：户型数字 ----------
# 「两居」「两室」是最高频的口语说法，早先的字符类只写了 [一二三四五1-5]，
# 于是「南十里居 两居」抽出的 filters 里只有 address、**没有 rooms**——
# 户型约束根本没进排序，top1 落到该小区的三居上（v3 评测集 mixed 层抓到的问题）。
_CN_ROOMS = {
    "一": "1", "二": "2", "两": "2", "俩": "2",
    "三": "3", "四": "4", "五": "5", "六": "6",
}
_ROOMS_RE = re.compile(r"([一二两俩三四五六1-6])\s*(?:居|室)")
# 省略式并列：「两三居」「两、三居」「2-3室」——第二个「居/室」被省掉，靠相邻两个数字识别。
# 注意这不会误伤「2室1厅」：那里两个数字中间隔着「室」，不满足"两个数字相邻"。
_ROOMS_RANGE_RE = re.compile(
    r"([一二两俩三四五六1-6])\s*[、或和/~-]?\s*([一二两俩三四五六1-6])\s*(?:居|室)"
)

# ---------- 区间语义 ----------
# 「多少钱」「多少平」在中文里有四种含义，早先只认两种，还把面积偷偷乘了 0.8：
#   X 以内 / 以下 / 不到 / 不超过   → 上限
#   X 以上 / 超过 / 不低于          → 下限
#   X 左右 / 大约 / 差不多 / 上下   → 上下浮动 10%
#   200 到 400                      → 显式区间
# 修饰词判断必须讲究顺序：「不超过」含「超过」、「不低于」含「低于」，
# 所以先判否定形式，再判肯定形式，否则「不超过 300 万」会被当成「300 万以上」。
_RANGE_SEP = r"(?:\s*[-~到至]\s*)"
_ABOUT_WORDS = ("左右", "大约", "大概", "差不多", "上下", "出头", "附近")
_ABOUT_RATIO = 0.1
# 只说「65 平米两居」时的默认理解：用户能接受略小一点（早先乘 0.8 太宽，改为 0.9）
_DEFAULT_AREA_RATIO = 0.9


def _parse_numeric_range(query: str, unit_pattern: str, default: str = "max"):
    """把「300 万以下」「200 到 400 万」「300 万左右」解析成 (min, max)

    unit_pattern 是单位正则（价格 `万`、面积 `(?:㎡|平米|平方米)`）。
    default 为「没有任何修饰词」时的语义：价格默认当预算上限（max），
    面积默认当「至少这么大」（min）。
    解析不到返回 (None, None)。
    """
    q = query or ""
    # 第一个单位可以省略：「60 到 90 平米」「200 到 400 万」都这么写
    both = re.search(
        rf"(\d+(?:\.\d+)?)\s*(?:{unit_pattern})?\s*{_RANGE_SEP}(\d+(?:\.\d+)?)\s*(?:{unit_pattern})",
        q,
    )
    if both:
        lo, hi = sorted((float(both.group(1)), float(both.group(2))))
        return lo, hi

    one = re.search(rf"(\d+(?:\.\d+)?)\s*(?:{unit_pattern})", q)
    if not one:
        return None, None

    value = float(one.group(1))
    # 修饰词可能写在数字前面（「不低于 300 万」），也可能在后面（「300 万以下」）
    ctx = q[max(0, one.start() - 4): one.end() + 8]

    # 「首付 100 万」说的是首付，不是总价——当成总价上限会把剩下的房源全筛掉
    if "首付" in ctx:
        return None, None

    if any(w in ctx for w in ("不低于", "不少于")):
        return value, None
    if any(w in ctx for w in ("不超过", "不到", "以内", "以下", "低于", "之内")):
        return None, value
    if any(w in ctx for w in ("以上", "超过", "起", "往上")):
        return value, None
    if any(w in ctx for w in _ABOUT_WORDS):
        return round(value * (1 - _ABOUT_RATIO), 2), round(value * (1 + _ABOUT_RATIO), 2)

    if default == "min":
        return round(value * _DEFAULT_AREA_RATIO, 2), None
    return None, value


def _extract_rooms(query: str):
    """抽出问题里的全部户型要求：'两居或三居' → ['2室', '3室']

    早先用 re.search 只取第一个，第二选项被直接丢掉；'开间' 完全不认。
    现在还要处理省略式并列：'两三居' '两、三居' '2-3室'——第二个「居/室」被省掉了，
    单个正则只会抓到「三居」，「两居」整个丢掉（E022 就是栽在这儿）。
    """
    q = query or ""
    rooms: List[str] = []

    # ① 省略式并列：「两三居」中间的「居」省了，靠相邻两个数字来识别
    for match in _ROOMS_RANGE_RE.finditer(q):
        for ch in match.group(1, 2):
            tag = f"{_CN_ROOMS.get(ch, ch)}室"
            if tag not in rooms:
                rooms.append(tag)

    # ② 常规写法：「两居」「三室」各出现一次
    for match in _ROOMS_RE.finditer(q):
        tag = f"{_CN_ROOMS.get(match.group(1), match.group(1))}室"
        if tag not in rooms:
            rooms.append(tag)

    if not rooms and "开间" in q:
        # 开间在库里记作 0 室 1 厅或 1 室 0 厅，两种都要认
        rooms = ["0室", "1室"]
    return rooms

SUPERVISOR_PROMPT = """你是多智能体系统的调度中枢（Supervisor）。请判断下面这个房产问题应该交给哪位专家处理，只输出一个英文标识，不要解释：
- house_search：找房源、按区域/预算/户型/面积等条件筛房
- knowledge：购房政策、税费、贷款、过户流程、产权等知识性问题
- analytics：房价走势、未来涨跌、行情、租金回报、是否值得买等分析性问题
- hybrid：同时涉及多个方面，或无法确定

只输出 house_search / knowledge / analytics / hybrid 之一。"""

FILTER_PROMPT = """你是房源检索条件抽取器。请从用户问题中抽取结构化筛选条件，输出 JSON（没有的字段给 null）：
{"region": "北京行政区县名或null", "price_min": 数字或null, "price_max": 数字或null, "rooms": "如2室1厅或null", "area_min": 数字或null, "area_max": 数字或null, "keyword": "小区名/地铁/关键词或null"}
单位：价格统一为万元，面积为平方米。只输出 JSON，不要解释。"""

# ---------- 追问改写 Prompt ----------

REWRITE_SYSTEM_PROMPT = """你是检索问题改写助手。根据对话历史，把用户最新的一句话改写成可以独立检索的完整问题。
规则：
1. 用户说「第一套」「第二套」时，指的是上一条助手回复中推荐列表里的对应房源，
   改写时必须写出该房源的小区名称和价格，例如「首邑溪谷 272万 那套房的户型是多少」
2. 用户说「这个」「那套」「它」「刚才那个」时，替换成上文中明确提到的房源或条件
3. 保留用户之前提出的所有约束条件（区域、预算、户型、面积等）
4. 只输出改写后的问题本身，不要解释、不要加引号、不要分点
5. 如果原问题已经足够完整，原样输出"""

REWRITE_USER_PROMPT = """对话历史：
{history}

最新问题：{question}

改写后的问题："""


class RAGState(TypedDict, total=False):
    """图状态（多 Agent 版：route / agent_trace / analytics_text 为新增字段）"""

    question: str
    conversation_id: str
    stream: bool
    # 多轮记忆（add_messages 负责追加与去重）
    history: Annotated[List[BaseMessage], add_messages]
    # 路由结果
    need_retrieval: bool
    route: str
    route_reason: str
    agent_trace: List[dict]
    # 改写后用于向量检索的问题（追问补全）
    search_query: str
    # 结构化条件：抽一次，全链路共用（house_search 与 retrieve 不再各抽一套）
    filters: dict
    # 检索结果
    house_docs: List[Document]
    doc_docs: List[Document]
    house_metas: List[dict]
    sources: List[dict]
    # 专家（行情分析）产出的结构化结论文本
    analytics_text: str
    # 结构化检索条件说明（告诉模型「这是按条件筛的」）
    notes: str
    # 输出
    answer: str


def _get_writer():
    """安全获取 LangGraph 流式写入器（非流式上下文返回 None）"""
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:
        return None


def _parse_json(text: str) -> Optional[dict]:
    """从模型输出里抠出第一个 JSON 对象（模型偶尔会包 ```json 或加解释）"""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# 小区名词典缓存：用于判断「这个问题是不是在问某个具体小区」
_ADDRESS_VOCAB: Dict[str, tuple] = {"size": -1, "vocab": ()}


def _address_vocab() -> tuple:
    """库内真实小区名（按长度倒序，长名优先匹配）

    直接取自 BM25 索引的元数据，不另维护一份名单——避免名单和库不同步。
    """
    bm25 = get_house_bm25()
    if not bm25:
        return ()
    if _ADDRESS_VOCAB["size"] == bm25.n:
        return _ADDRESS_VOCAB["vocab"]

    names = {(meta or {}).get("address") for _content, meta in bm25.raw.values()}
    vocab = tuple(sorted((n for n in names if n and len(n) >= 2), key=len, reverse=True))
    _ADDRESS_VOCAB.update(size=bm25.n, vocab=vocab)
    return vocab


class RAGGraph:
    """LangGraph 版多智能体问答流程"""

    def __init__(self):
        self.top_k = settings.rag_top_k
        self.score_threshold = settings.rag_score_threshold
        # 召回 → 融合 → 精排 → 多样性 各段的容量参数（集中放在 config.py）
        self.dense_recall_k = settings.rag_dense_recall_k
        self.sparse_recall_k = settings.rag_sparse_recall_k
        self.fuse_top_n = settings.rag_fuse_top_n
        self.diversity = settings.rag_diversity
        self._graph = self._build()

    # ---------- 构图 ----------

    def _build(self):
        builder = StateGraph(RAGState)
        builder.add_node("analyze_intent", self.analyze_intent)
        builder.add_node("supervisor", self.supervisor)
        builder.add_node("rewrite_query", self.rewrite_query)
        builder.add_node("house_search", self.house_search)
        builder.add_node("knowledge", self.knowledge)
        builder.add_node("retrieve", self.retrieve)
        builder.add_node("analytics", self.analytics)
        builder.add_node("generate", self.generate)

        builder.add_edge(START, "analyze_intent")
        builder.add_conditional_edges(
            "analyze_intent",
            self._route_after_intent,
            {"supervisor": "supervisor", "generate": "generate"},
        )
        builder.add_conditional_edges(
            "supervisor",
            self._route_after_supervisor,
            {"house_search": "house_search", "rewrite": "rewrite_query",
             "analytics": "analytics"},
        )
        builder.add_conditional_edges(
            "rewrite_query",
            self._route_after_rewrite,
            {"knowledge": "knowledge", "retrieve": "retrieve"},
        )
        for expert in ("house_search", "knowledge", "retrieve", "analytics"):
            builder.add_edge(expert, "generate")
        builder.add_edge("generate", END)

        graph = builder.compile(checkpointer=MemorySaver())
        logger.info("LangGraph multi-agent workflow compiled")
        return graph

    @property
    def graph(self):
        return self._graph

    @staticmethod
    def _route_after_intent(state: RAGState) -> str:
        return "supervisor" if state.get("need_retrieval", True) else "generate"

    @staticmethod
    def _route_after_supervisor(state: RAGState) -> str:
        route = state.get("route") or ROUTE_HYBRID
        if route in (ROUTE_HOUSE, ROUTE_ANALYTICS):
            return route
        return "rewrite"

    @staticmethod
    def _route_after_rewrite(state: RAGState) -> str:
        return "knowledge" if (state.get("route") == ROUTE_KNOWLEDGE) else "retrieve"

    # ---------- 节点 1：意图分析 ----------

    async def analyze_intent(self, state: RAGState) -> dict:
        question = (state.get("question") or "").strip()
        lowered = question.lower()

        is_chitchat = len(lowered) <= 12 and any(k in lowered for k in CHITCHAT_KEYWORDS)
        need_retrieval = bool(question) and not is_chitchat

        logger.info(f"[Agent] analyze_intent -> need_retrieval={need_retrieval}, q={question[:30]}")
        return {
            "need_retrieval": need_retrieval,
            "history": [HumanMessage(content=question)] if question else [],
        }

    # ---------- 节点 2：Supervisor 调度 ----------

    async def supervisor(self, state: RAGState) -> dict:
        """决定把问题交给哪个专家：规则优先，歧义时才问模型"""
        question = (state.get("question") or "").strip()
        route, reason = self._rule_route(question)

        if route == ROUTE_HYBRID:
            llm_route = await self._llm_route(question)
            if llm_route:
                route, reason = llm_route, "LLM 分类"

        logger.info(f"[Agent] supervisor -> {route}（{reason}）")
        trace = list(state.get("agent_trace") or [])
        trace.append({"agent": "supervisor", "route": route, "label": ROUTE_LABELS.get(route, route),
                      "reason": reason})

        if state.get("stream"):
            writer = _get_writer()
            if writer:
                writer({"type": "agent_step", "agent": "supervisor", "route": route,
                        "label": ROUTE_LABELS.get(route, route), "reason": reason})

        return {"route": route, "route_reason": reason, "agent_trace": trace}

    # ---------- 节点 3：追问改写 ----------

    async def rewrite_query(self, state: RAGState) -> dict:
        """结合对话历史改写追问，避免用「第一套的户型」这类省略句直接检索

        没有历史（首轮对话）时直接返回原问题，不额外消耗一次 LLM 调用。
        """
        question = (state.get("question") or "").strip()
        history = list(state.get("history") or [])

        # history 的最后一条是本轮问题（analyze_intent 刚写入），改写时排除
        prev_messages = history[:-1][-4:]
        if len(prev_messages) < 2:
            return {"search_query": question}

        try:
            # 助手回复通常较长（含推荐列表），保留更多内容以便正确解析「第一套」所指
            history_text = "\n".join(
                f"{'用户' if getattr(m, 'type', '') == 'human' else '助手'}：{(m.content or '')[:800]}"
                for m in prev_messages
                if getattr(m, "content", None)
            )
            prompt = REWRITE_USER_PROMPT.format(history=history_text, question=question)
            rewritten = await get_llm_service().generate(prompt, REWRITE_SYSTEM_PROMPT)
            rewritten = (rewritten or "").strip().splitlines()[0].strip() if rewritten else ""

            # 改写结果异常（为空或过长）时回退原问题
            if not rewritten or len(rewritten) > 200:
                rewritten = question

            logger.info(f"[Agent] rewrite_query: {question} -> {rewritten}")
            return {"search_query": rewritten}
        except Exception as e:
            logger.warning(f"[Agent] rewrite_query failed, fallback to original: {e}")
            return {"search_query": question}

    # ---------- 专家 A：结构化房源检索 ----------

    async def house_search(self, state: RAGState) -> dict:
        """按区域/预算/户型/面积走 SQL 精确过滤（向量检索对硬条件不敏感）"""
        query = state.get("search_query") or state.get("question", "")
        # 条件全链路只抽一次：别的节点已经抽过就直接复用
        filters = (state.get("filters") or {}).copy()
        if not filters:
            filters = await self._extract_filters(query)
        logger.info(f"[Agent] house_search filters={filters}")

        house_metas: List[dict] = []
        sources: List[dict] = []
        db = None
        try:
            from app.database import SessionLocal
            from app.services.house_service import HouseService

            db = SessionLocal()
            houses, total = HouseService(db).get_house_list(
                page=1, page_size=8,
                keyword=filters.get("keyword"),
                region=filters.get("region"),
                rooms=filters.get("rooms"),
                price_min=filters.get("price_min"),
                price_max=filters.get("price_max"),
            )
            # 面积是字符串（"56平米"），SQL 侧不便过滤，这里在内存里再筛一遍
            a_min, a_max = filters.get("area_min"), filters.get("area_max")
            if a_min or a_max:
                from app.utils.price_utils import parse_area_to_sqm
                kept = []
                for h in houses:
                    area = parse_area_to_sqm(h.area) or 0
                    if a_min and area and area < a_min:
                        continue
                    if a_max and area and area > a_max:
                        continue
                    kept.append(h)
                houses = kept or houses

            for i, h in enumerate(houses):
                d = h.to_dict()
                house_metas.append({
                    "house_id": d.get("id"), "title": d.get("title") or "",
                    "address": d.get("address") or "", "price": d.get("price") or "",
                    "rooms": d.get("rooms") or "", "area": d.get("area") or "",
                    "region": d.get("region") or "", "direction": d.get("direction") or "",
                    "floor": d.get("floor") or "",
                })
                sources.append({
                    "source_id": f"house_{i}",
                    "source_type": "house",
                    "title": d.get("title") or "房源",
                    "content_preview": f"{d.get('region') or ''} {d.get('address') or ''} "
                                       f"{d.get('rooms') or ''} {d.get('area') or ''} {d.get('price') or ''}".strip(),
                    "score": 1.0,
                    "house_id": d.get("id"),
                })
            logger.info(f"[Agent] house_search -> 命中 {len(house_metas)} 套（数据库共 {total} 套满足条件）")
        except Exception as e:
            logger.warning(f"[Agent] house_search failed: {e}")
        finally:
            if db is not None:
                db.close()

        return {"house_metas": house_metas, "sources": sources, "filters": filters,
                "notes": self._filter_note(filters, house_metas)}

    # ---------- 专家 B：知识库检索 ----------

    async def knowledge(self, state: RAGState) -> dict:
        """只查知识文档库：政策、税费、流程这类问题不该混入房源结果"""
        query = state.get("search_query") or state.get("question", "")
        doc_docs: List[Document] = []
        sources: List[dict] = []

        try:
            doc_pairs = get_doc_store().similarity_search_with_score(
                query, k=self.top_k, score_threshold=self.score_threshold
            )
            doc_docs = [doc for doc, _ in doc_pairs]
            for i, (doc, score) in enumerate(doc_pairs):
                sources.append({
                    "source_id": f"doc_{i}",
                    "source_type": "document",
                    "title": doc.metadata.get("document_name", "文档"),
                    "content_preview": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
                    "score": round(float(score), 4),
                    "document_name": doc.metadata.get("document_name"),
                })
        except Exception as e:
            logger.warning(f"[Agent] knowledge 检索失败: {e}")

        logger.info(f"[Agent] knowledge -> 文档 {len(doc_docs)} 条")
        return {"doc_docs": doc_docs, "sources": sources}

    # ---------- 专家 C：行情与走势分析 ----------

    async def analytics(self, state: RAGState) -> dict:
        """调 LSTM 走势预测 + 行情统计，产出可直接引用的结构化结论"""
        question = state.get("search_query") or state.get("question", "")
        region = self._detect_region(question)
        lines: List[str] = []
        sources: List[dict] = []
        db = None

        try:
            from app.database import SessionLocal
            from app.services.market_stats import MarketStatsService
            from app.services.trend_service import TrendService

            db = SessionLocal()
            trend = TrendService(db)

            if region:
                data = trend.get_trend(region, 6)
                hist = data.get("history") or []
                preds = data.get("predictions") or []
                lines.append(f"【{region} 房价走势（LSTM 预测）】")
                if hist:
                    lines.append(f"- 最新月度均价：{hist[-1]['price']} 万元（{hist[-1]['month']}）")
                if data.get("mom_change") is not None:
                    lines.append(f"- 环比上月：{data['mom_change']}%")
                if data.get("predicted_change") is not None:
                    lines.append(f"- 未来 6 个月预测涨跌：{data['predicted_change']}%")
                if preds:
                    lines.append("- 逐月预测：" + "、".join(
                        f"{p['month']} {p['price']} 万" for p in preds[:6]))
                lines.append(f"- 预测方法：{data.get('method', 'LSTM')}")
                sources.append({
                    "source_id": "analytics_0",
                    "source_type": "analytics",
                    "title": f"{region} 房价走势与预测",
                    "content_preview": "；".join(lines[-4:]),
                    "score": 1.0,
                })

                stats = MarketStatsService(db).get_region_stats(region)
                if stats.get("median_unit_price"):
                    lines.append(f"\n【{region} 真实行情】")
                    lines.append(f"- 样本量：{stats.get('sample_count')} 套")
                    lines.append(f"- 单价中位数：{stats.get('median_unit_price')} 万/㎡"
                                 f"（P25 {stats.get('unit_p25')} ~ P75 {stats.get('unit_p75')}）")
                    lines.append(f"- 总价中位数：{stats.get('median_total_price')} 万"
                                 f"（P25 {stats.get('total_p25')} ~ P75 {stats.get('total_p75')}）")
            else:
                rows = trend.regions()[:6]
                lines.append("【北京各区域最新均价（未指定区域，给出总览）】")
                for r in rows:
                    lines.append(f"- {r['name']}：{r['latest_price']} 万元")
                lines.append("如需具体区域走势，请补充区名（例如「海淀区未来半年走势」）。")
                sources.append({
                    "source_id": "analytics_0",
                    "source_type": "analytics",
                    "title": "北京各区域最新均价",
                    "content_preview": "；".join(lines[1:5]),
                    "score": 1.0,
                })
        except Exception as e:
            logger.warning(f"[Agent] analytics failed: {e}")
            lines = []
        finally:
            if db is not None:
                db.close()

        text = "\n".join(lines)
        logger.info(f"[Agent] analytics -> {len(text)} 字, region={region}")
        return {"analytics_text": text, "sources": sources}

    # ---------- 专家 D：混合兜底（房源库混合召回 + 知识库向量召回） ----------

    @classmethod
    def _rrf_fuse(cls, rankings: List[List[str]], weights: List[float] = None) -> Dict[str, float]:
        """加权 Reciprocal Rank Fusion：只吃名次，不吃分数

        稠密路给 cosine（0~1），稀疏路给 BM25（无上界），直接加权平均还得先调
        归一化系数；RRF 用 w/(k+rank) 抹平尺度差异，并奖励「两路都靠前」的候选。

        为什么要加权：等权时 BM25 排第 1 的候选会被稠密排第 5 的候选稀释
        （1/61+1/65 < 1/61+1/63），实测 strict@1 反而比纯 BM25 更差（0.700 < 0.767）。
        字面型查询（含库内小区名）把稀疏权重提到 1.6，就是修这个问题。
        """
        k = settings.rag_rrf_k
        ws = weights or [1.0] * len(rankings)
        scores: Dict[str, float] = {}
        for ranking, w in zip(rankings, ws):
            for rank, key in enumerate(ranking, 1):
                if not key:
                    continue
                scores[key] = scores.get(key, 0.0) + w / (k + rank)
        return scores

    @staticmethod
    def _matched_addresses(query: str) -> List[str]:
        """query 里命中的库内小区名（长名优先，避免「北京」抢先命中「北京北」）"""
        q = query or ""
        hits: List[str] = []
        for addr in _address_vocab():
            if addr in q and not any(addr in h for h in hits):
                hits.append(addr)
        return hits

    def _is_literal_query(self, query: str) -> bool:
        """问题里是否出现了库内真实小区名——决定加权 RRF 的权重方向"""
        return bool(self._matched_addresses(query))

    @staticmethod
    def _constraint_eval(doc: Document, filters: dict, relaxed: tuple = ()) -> dict:
        """评估一条候选对结构化条件的满足情况

        实现已抽到 `app.services.house_filters.eval_constraints`——线上检索和离线评测
        必须用同一份判定，否则会出现「评测说答对了、线上说答错了」的假分数。
        这里只留一层转发，调用点（约束分层）不用改。
        """
        return eval_constraints(doc, filters, relaxed)

    @staticmethod
    def _diversify(candidates: List[dict]) -> List[dict]:
        """同一小区只让最高分那条优先入选，其余排到后面

        这是 MMR「多样性」的实用版：目标是让用户看到不同小区的横向对比，
        而不是同一个小区挤满 5 个名额（实测「南十里居 两居」曾返回 8 条同小区）。
        按小区名去重比算向量相似度更直接，也不用额外的 embedding 调用。
        """
        primary, secondary, seen = [], [], set()
        for c in candidates:
            addr = (c["doc"].metadata or {}).get("address")
            if addr and addr in seen:
                secondary.append(c)
            else:
                if addr:
                    seen.add(addr)
                primary.append(c)
        return primary + secondary

    def _house_hybrid_recall(self, query: str, filters: dict = None) -> List[dict]:
        """房源库混合召回：稠密 + 稀疏 → 加权 RRF → 精排 → 约束分层 → 小区级去重

        分工（这一版最重要的设计判断）：
            加权 RRF 只负责「别漏」——生成一个相关性候选池；
            最终顺序由「结构化条件满足度」拍板——因为判断答得对不对的标准是
            「小区对 + 两居 + 价格在范围内」，而不是「语义相似」。
            之前把 RRF 当最终排序依据，所以 strict@1 反而低于纯 BM25（0.733 < 0.767）。

        filters 为 None 时退化为纯相关性排序，与改造前行为一致。
        """
        dense_pairs: List[Tuple[Document, float]] = []
        try:
            dense_pairs = get_house_store().similarity_search_with_score(
                query, k=self.dense_recall_k, score_threshold=self.score_threshold
            )
        except Exception as e:
            logger.warning(f"[Agent] 房源稠密检索失败: {e}")

        sparse_hits: List[Tuple[str, float]] = []
        bm25 = get_house_bm25()
        if bm25:
            try:
                sparse_hits = bm25.search(query, k=self.sparse_recall_k)
            except Exception as e:
                logger.warning(f"[Agent] 房源稀疏检索失败: {e}")

        if not dense_pairs and not sparse_hits:
            return []

        # 两路各自的名次表（key 用 house_id 对齐，同一候选只计一次）
        dense_map: Dict[str, Tuple[Document, float]] = {}
        for doc, score in dense_pairs:
            key = _house_doc_key(doc.metadata)
            if key and key not in dense_map:
                dense_map[key] = (doc, float(score))
        sparse_map = dict(sparse_hits)

        literal = self._is_literal_query(query)
        if literal:
            weights = [settings.rag_rrf_w_dense_literal, settings.rag_rrf_w_sparse_literal]
        else:
            weights = [settings.rag_rrf_w_dense, settings.rag_rrf_w_sparse]

        fused = self._rrf_fuse(
            [list(dense_map.keys()), list(sparse_map.keys())], weights=weights
        )

        # ① 融合：只取前 fuse_top_n 条进精排（精排是 CPU 上最贵的一步）
        #
        #    filters 非空时先看一条更宽的切片（4 倍容量），因为「硬约束合格」的候选
        #    可能压根不在相关性前 20 里。「预算三百万以内的两居」就是这种情况：
        #    稠密路按语义相似度返回的全是贵的房，便宜房只有 BM25 零星捞到几条，
        #    RRF 一融合就被挤到 20 名之外——池子里没有对的房，后面排序再准也没用
        #    （评测里 semantic 层两题「零覆盖」就是这么来的：库里有 633 套合格的，
        #    top-5 却一条都没有）。宽切片里把硬约束合格的候选提前占位（占一半名额），
        #    其余名额仍按相关性补齐，组内相关性顺序不变，精排会再按 query 重排。
        ranked = sorted(fused.items(), key=lambda kv: -kv[1])
        # 只有「没点名小区但带硬约束」的查询才需要全量融合结果 + 准入，
        # 其余情况多建候选只是一次白跑（精排仍然只吃 fuse_top_n 条）。
        # 用全量而不是 4 倍切片：fused 最多 dense_k + sparse_k = 100 个 key，
        # 合格房可能正好排在被切掉的那 20 名里——「预算三百万以内的两居」
        # 扩到全量 38266 条后就是这样又掉回零覆盖的。
        constraint_only = bool(filters) and not (filters or {}).get("address")
        wide = ranked if constraint_only else ranked[: self.fuse_top_n]

        candidates: List[dict] = []
        for key, rrf_score in wide:
            pair = dense_map.get(key)
            if pair:
                doc, dense_score = pair
            else:
                # 只被 BM25 命中：正文和元数据从稀疏索引里取回
                content, metadata = (bm25.raw.get(key) if bm25 else None) or ("", {})
                if not content:
                    continue
                doc, dense_score = Document(page_content=content, metadata=metadata), None

            paths = []
            if dense_score is not None:
                paths.append("dense")
            if key in sparse_map:
                paths.append("sparse")

            candidates.append({
                "doc": doc,
                "rrf_score": round(rrf_score, 6),
                "dense_score": round(dense_score, 4) if dense_score is not None else None,
                "bm25_score": round(sparse_map[key], 4) if key in sparse_map else None,
                "rerank_score": None,
                "retrieval": "+".join(paths),
            })

        # ①' 准入：宽切片里硬约束合格的候选先占位（最多一半名额），其余按相关性补齐。
        #     只对「没点名小区」的查询生效。点名了小区时相关性已经锚定在那个小区，
        #     硬塞别的小区的便宜房会挤掉真正的答案——实测 mixed 层合格精度从 0.491
        #     掉到 0.436，而这层恰恰是「点名小区 + 户型/价格」的主场，不能为语义层
        #     的收益付这笔账。只点名小区的字面查询里硬约束判定恒通过，本来也无需干预。
        if constraint_only and len(candidates) > self.fuse_top_n:
            ok_slots = self.fuse_top_n // 2
            picked, rest = [], []
            for c in candidates:
                if self._constraint_eval(c["doc"], filters, ())["hard_ok"]:
                    picked.append(c)
                else:
                    rest.append(c)
            candidates = (picked[:ok_slots] + rest)[: self.fuse_top_n]

        # ② 精排：cross-encoder 对 query-doc 整条打分；关闭或失败时保留融合顺序
        reranked = get_reranker().rerank(
            query, [c["doc"] for c in candidates], top_n=len(candidates)
        )
        if reranked:
            by_object = {id(c["doc"]): c for c in candidates}
            merged = []
            for doc, score in reranked:
                item = by_object.get(id(doc))
                if item:
                    item["rerank_score"] = round(float(score), 4)
                    merged.append(item)
            if merged:
                candidates = merged

        # ③ 约束分层：在「是否答对」上拍板
        #
        #    排序键 = (是否命中点名小区, 硬约束是否通过, 软约束满足度)，三级都是稳定排序，
        #    所以同一组内仍然保留 ② 的相关性顺序。
        #    hard_ok 为 False 的候选不删除、只下沉——「条件太严也得给答案」，
        #    同时把放宽了哪几个维度记进 relaxed，回答时如实说明。
        relaxed_dims: tuple = ()
        if filters:
            # 渐进放宽：从「不放宽」起，一级一级降标准，直到**交出去的前 top_k 条**里
            # 合格候选够数为止。
            #
            # 早先的判据是「候选池里 hard_ok 的条数 >= top_k」。池子是全局 20 条，
            # 别的小区有几套便宜房就把计数撑满了，于是「公园大道 500 万以内」明明 7 套
            # 全在 1580 万以上，也认为不需要放宽，照样把超预算的排前五——用户看到的
            # 是五套完全买不起的房，系统却说自己「按条件筛过了」。
            # 判据必须落在真正要交出去的那几条上，而不是整个池子。
            dims = []
            if filters.get("area_min") or filters.get("area_max"):
                dims.append("area")
            if filters.get("price_min") or filters.get("price_max"):
                dims.append("price")
            if filters.get("region"):
                dims.append("region")

            levels = [()]
            for dim in dims:
                levels.append(levels[-1] + (dim,))

            pool = candidates
            # 先记一遍「不放宽时是否达标」。放宽之后用得上：真能买的房要排在
            # 「凑数才被放进来」的房前面，否则用户看到的会是五套完全超预算的房子，
            # 而真正在预算内的那两套被挤到后面。
            for c in pool:
                strict = self._constraint_eval(c["doc"], filters, ())
                c["strict_hard"] = strict["hard_ok"]
                c["strict_soft"] = float(strict.get("soft") or 0.0)

            need_addr = bool(filters.get("address"))

            # 「池内可达的合格数」：这一池子里到底有几条能答对。
            #
            # 放宽判据必须拿它做上限，不能直接拿 top_k。早先用的是
            # 「top_k 条全合格才不放宽」，于是「公园大道 双龙南里 400 万以内的两居」
            # 只有 1 套合格时，永远凑不满 5 条 → 必然宣布放宽，可交付的第一条
            # 其实就在预算内：用户看到「已放宽价格」配一套 380 万的房子，自相矛盾。
            # 现在：池里有 1 条合格就只要求交出去 1 条，够了就不放宽。
            reachable = sum(
                1 for c in pool
                if (not need_addr or c.get("address"))
                and c.get("strict_hard")
                and float(c.get("strict_soft") or 0.0) >= 1.0
            )
            need = max(1, min(self.top_k, reachable))

            for level in levels:
                evaluated = []
                for c in pool:
                    c.update(self._constraint_eval(c["doc"], filters, level))
                    evaluated.append(c)

                ordered = sorted(
                    evaluated,
                    key=lambda c: (
                        0 if c.get("address") else 1,
                        0 if c.get("strict_hard") else 1,
                        0 if c.get("hard_ok") else 1,
                        -float(c.get("soft") or 0.0),
                    ),
                )

                # 「合格」= 点名小区命中（用户点过小区时）+ 硬约束通过 + 软约束全中
                good = sum(
                    1 for c in ordered[: self.top_k]
                    if (not need_addr or c.get("address"))
                    and c.get("hard_ok")
                    and float(c.get("soft") or 0.0) >= 1.0
                )

                relaxed_dims = level
                candidates = ordered
                if good >= need:
                    break

        # ④ 多样性：只在「非字面查询」时按小区去重
        #
        #    这是被评测数据纠正过一次的设计。无差别去重时 distinct@5 从 0.532 涨到
        #    0.964，但 precision@5 从 0.940 崩到 0.260——因为用户点名「南十里居 两居」
        #    时，他要的就是南十里居的多套，把名额分给别的小区反而答非所问。
        #    所以：字面查询保留同小区的多套；语义查询（"预算不多的两居"）才需要
        #    覆盖不同小区做横向对比。
        if self.diversity and not literal:
            candidates = self._diversify(candidates)

        hits = candidates[: self.top_k]
        for h in hits:
            h["relaxed"] = list(relaxed_dims)

        both = sum(1 for h in hits if h["retrieval"] == "dense+sparse")
        constraint_note = ""
        if filters:
            addr_hit = sum(1 for h in hits if h.get("address"))
            hard_hit = sum(1 for h in hits if h.get("hard_ok"))
            constraint_note = f"约束[小区命中 {addr_hit} / 硬约束通过 {hard_hit}"
            if relaxed_dims:
                constraint_note += f" / 放宽 {'+'.join(relaxed_dims)}"
            constraint_note += "] "

        logger.info(
            f"[Agent] 房源召回: 稠密 {len(dense_map)} / 稀疏 {len(sparse_hits)} "
            f"→ 加权RRF({('字面' if literal else '语义')}) {len(fused)} "
            f"→ 精排{'开' if reranked else '关'} → {constraint_note}取 {len(hits)}"
            f"（双路命中 {both}）"
        )
        return hits

    async def retrieve(self, state: RAGState) -> dict:
        query = state.get("search_query") or state.get("question", "")
        house_docs: List[Document] = []
        doc_docs: List[Document] = []
        sources: List[dict] = []

        # 结构化条件：**真的参与排序**，而且与 house_search 共用同一份。
        # 早先这里只走正则层，house_search 走「正则 + 模型」，同一个问题两条路各说各话
        # （「南十里居 两居」在 SQL 路是两居、在混合召回路却只剩小区名）。
        # 现在：state 里有就复用；没有就正则先抽，口语化条件抽不出来时才补一次模型。
        filters = (state.get("filters") or {}).copy()
        if not filters:
            filters = self._regex_filters(query)
            if self._needs_model_filters(query, filters):
                filters = await self._extract_filters(query)
                logger.info(f"[Agent] 条件正则不全，已用模型补齐: {filters}")

        # 房源路：稠密 + 稀疏 → 加权 RRF 候选池 → 精排 → 约束分层
        hits = self._house_hybrid_recall(query, filters=filters)
        for i, hit in enumerate(hits):
            doc = hit["doc"]
            house_docs.append(doc)
            sources.append({
                "source_id": f"house_{i}",
                "source_type": "house",
                "title": doc.metadata.get("title", "房源"),
                "content_preview": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
                # score 是 cosine 相似度（只有稀疏路命中时为 None），
                # 最终排序依据是约束分层，rrf/rerank 只在分层内部起作用
                "score": hit["dense_score"],
                "rrf_score": hit["rrf_score"],
                "rerank_score": hit.get("rerank_score"),
                "retrieval": hit["retrieval"],
                "constraint": {
                    "address": hit.get("address"),
                    "hard_ok": hit.get("hard_ok"),
                    "soft": round(float(hit.get("soft") or 0.0), 3),
                    "relaxed": hit.get("relaxed") or [],
                },
                "house_id": doc.metadata.get("house_id"),
            })

        try:
            doc_pairs = get_doc_store().similarity_search_with_score(
                query, k=self.top_k, score_threshold=self.score_threshold
            )
            doc_docs = [doc for doc, _ in doc_pairs]
            for i, (doc, score) in enumerate(doc_pairs):
                sources.append({
                    "source_id": f"doc_{i}",
                    "source_type": "document",
                    "title": doc.metadata.get("document_name", "文档"),
                    "content_preview": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
                    "score": round(float(score), 4),
                    "document_name": doc.metadata.get("document_name"),
                })
        except Exception as e:
            logger.warning(f"[Agent] 知识文档检索失败: {e}")

        logger.info(f"[Agent] retrieve -> 房源 {len(house_docs)} 条, 文档 {len(doc_docs)} 条")

        # 流式模式下，检索完成即可先推送 sources
        if state.get("stream"):
            writer = _get_writer()
            if writer:
                writer({"type": "retrieve_done", "sources": sources})

        # 约束放宽必须说给生成层听，否则模型会把「放宽后的结果」讲成「完全符合条件」
        relaxed = (hits[0].get("relaxed") if hits else None) or []
        house_metas = [doc.metadata for doc in house_docs]
        notes = (state.get("notes") or "").strip()

        # 混合路径也要告诉模型「这是按条件筛的」，否则它会把「筛不到」讲成「没有这种房子」
        filter_note = self._filter_note(filters, house_metas)
        if filter_note:
            notes = f"{notes}\n{filter_note}".strip() if notes else filter_note

        if relaxed:
            dims_cn = "、".join(_FILTER_DIM_CN.get(d, d) for d in relaxed)
            relax_note = (
                f"【约束已放宽】原始条件里的{dims_cn}过严，已放宽该维度才凑够候选。"
                "回答时必须说明这一点，不要声称完全符合原条件。"
            )
            notes = f"{notes}\n{relax_note}".strip() if notes else relax_note

        return {
            "house_docs": house_docs,
            "doc_docs": doc_docs,
            "house_metas": house_metas,
            "sources": sources,
            "filters": filters,
            "notes": notes,
        }

    # ---------- 节点 8：汇总生成 ----------

    async def generate(self, state: RAGState) -> dict:
        llm_service = get_llm_service()
        question = state.get("question", "")
        houses = state.get("house_metas") or []
        docs = [doc.page_content for doc in (state.get("doc_docs") or [])]
        analytics_text = (state.get("analytics_text") or "").strip()
        notes = (state.get("notes") or "").strip()
        history = list(state.get("history") or [])

        has_context = bool(houses or docs or analytics_text or notes)
        writer = _get_writer() if state.get("stream") else None

        # 组装消息：System + 历史对话 + 当前问题
        system_prompt = RAG_SYSTEM_PROMPT if has_context else GENERAL_SYSTEM_PROMPT
        messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]
        # 历史里最后一条是本轮的 HumanMessage，单独处理
        for msg in history[:-1][-6:]:
            messages.append(msg)

        if has_context:
            context_parts = []
            if notes:
                context_parts.append(notes)
            if houses or docs:
                context_parts.append(build_context(houses, docs))
            if analytics_text:
                context_parts.append("【行情与走势数据（由行情分析 Agent 计算）】\n" + analytics_text)
            user_content = (
                f"用户问题：{question}\n\n" + "\n\n".join(context_parts) +
                "\n\n请根据以上信息回答用户问题；引用数据时说明来自走势模型还是房源库。"
            )
        else:
            user_content = question
        messages.append(HumanMessage(content=user_content))

        answer = ""
        try:
            async for chunk in llm_service.llm.astream(messages):
                text = llm_service._chunk_text(chunk)
                if not text:
                    continue
                answer += text
                if writer:
                    writer({"type": "delta", "content": text})
        except Exception as e:
            logger.error(f"[Agent] generate failed: {e}")
            answer = f"抱歉，回答生成出现问题：{e}"
            if writer:
                writer({"type": "delta", "content": answer})
            if not has_context:
                answer = "抱歉，我没有找到相关的房源信息，您可以尝试换个关键词，或先在系统中添加房源数据。"

        logger.info(f"[Agent] generate -> {len(answer)} 字, 有上下文={has_context}, route={state.get('route')}")
        return {"answer": answer, "history": [AIMessage(content=answer)]}

    # ---------- Supervisor 辅助：规则路由 / LLM 兜底 ----------

    @staticmethod
    def _detect_region(text: str) -> Optional[str]:
        for name in BEIJING_DISTRICTS:
            if name in (text or ""):
                return name
        # 「海淀」「朝阳」这类省略「区」的说法
        for name in BEIJING_DISTRICTS:
            short = name[:-1]
            if len(short) >= 2 and short in (text or ""):
                return name
        return None

    @staticmethod
    def _rule_route(question: str) -> tuple:
        q = question or ""
        has_region = RAGGraph._detect_region(q) is not None
        has_price = bool(re.search(r"\d+\s*万", q))
        has_rooms = bool(_ROOMS_RE.search(q))
        has_area = bool(re.search(r"\d+\s*(㎡|平米|平方米)", q))

        if any(w in q for w in KNOWLEDGE_WORDS):
            return ROUTE_KNOWLEDGE, "命中政策/知识类关键词"
        if any(w in q for w in ANALYTICS_WORDS):
            return ROUTE_ANALYTICS, "命中行情/预测类关键词"
        vague = any(w in q for w in VAGUE_WORDS)

        if has_region or has_price or has_rooms or has_area:
            if vague:
                return ROUTE_HYBRID, "硬条件 + 泛化诉求，交给模型"
            return ROUTE_HOUSE, "识别到区域/预算/户型/面积等硬条件"
        if any(w in q for w in HOUSE_WORDS):
            if vague:
                return ROUTE_HYBRID, "找房关键词 + 泛化诉求，交给模型"
            return ROUTE_HOUSE, "命中找房类关键词"
        return ROUTE_HYBRID, "规则无法判定，交给模型"

    async def _llm_route(self, question: str) -> Optional[tuple]:
        """规则给不出结论时，让模型在四个专家里选一个（失败返回 None → hybrid）"""
        try:
            text = await get_llm_service().generate(question, SUPERVISOR_PROMPT)
            label = (text or "").strip().lower()
            for key in (ROUTE_HOUSE, ROUTE_KNOWLEDGE, ROUTE_ANALYTICS, ROUTE_HYBRID):
                if key in label:
                    return key, "LLM 分类"
        except Exception as e:
            logger.warning(f"[Agent] LLM 路由失败，回退 hybrid: {e}")
        return None

    def _regex_filters(self, query: str) -> dict:
        """纯正则的条件抽取（同步、零成本）

        这是全链路的第一层，也是唯一「一定跑」的一层：区域/价格/户型/面积/朝向/装修/
        电梯/小区名这些确定性信号都由它负责，恰好就是 strict@1 判断「答对没有」的维度。

        模型兜底不放在这里，而是由 `_needs_model_filters` 决定——只有在问题用了
        口语化条件（「预算不多」「一百来万」）而正则一个都没抽到时才补一次 LLM，
        这样常规问题不为「多说一句」付延迟。
        """
        q = query or ""
        filters = {
            "region": self._detect_region(q),
            "price_min": None, "price_max": None, "rooms": None,
            "area_min": None, "area_max": None, "keyword": None,
            "orientation": None, "decoration": None, "elevator": False,
            # query 里点名的小区：排序时的最高优先级信号（BM25 天然有、稠密没有）
            "address": self._matched_addresses(q),
        }

        filters["price_min"], filters["price_max"] = _parse_numeric_range(q, "万", default="max")
        filters["area_min"], filters["area_max"] = _parse_numeric_range(
            q, r"(?:㎡|平米|平方米)", default="min"
        )

        rooms = _extract_rooms(q)
        if rooms:
            # 单值仍存字符串（SQL 与旧逻辑都按单值取），多值才存列表
            filters["rooms"] = rooms[0] if len(rooms) == 1 else rooms

        if any(w in q for w in ("南北通透", "南北向", "南北")):
            filters["orientation"] = "南北"
        elif any(w in q for w in ("朝南", "南向")):
            filters["orientation"] = "南"
        elif any(w in q for w in ("朝北", "北向")):
            filters["orientation"] = "北"

        for word in ("精装", "简装", "毛坯"):
            if word in q:
                filters["decoration"] = word
                break

        if "电梯" in q:
            filters["elevator"] = True

        return filters

    @staticmethod
    def _needs_model_filters(query: str, filters: dict) -> bool:
        """正则抽完还值不值得多调一次模型？

        按维度判断：某个维度出现了口语化表达，而正则恰好在这个维度上是空的，才补一次。
        （「预算不多想买两居」抽到了户型、但价格仍是空——价格这一维就该补，
        早先只看「有没有抽到任何条件」会漏掉这种情况，结果推荐出 690 万的房子。）

        规规矩矩说「南十里居 两居」「500 万以内」的问题一次模型调用都不多花，
        混合召回不需要为少数口语说法让所有人一起等。
        """
        q = query or ""
        f = filters or {}

        if any(w in q for w in _LOOSE_PRICE_WORDS) and not (f.get("price_min") or f.get("price_max")):
            return True
        if any(w in q for w in _LOOSE_ROOMS_WORDS) and not f.get("rooms"):
            return True
        if any(w in q for w in _LOOSE_AREA_WORDS) and not (f.get("area_min") or f.get("area_max")):
            return True
        return False

    async def _extract_filters(self, query: str) -> dict:
        """抽取 SQL 筛选条件：正则先兜底，再让模型补齐（模型失败不影响正则结果）"""
        filters = self._regex_filters(query)

        # ---- 模型层 ----
        try:
            text = await get_llm_service().generate(query, FILTER_PROMPT)
            data = _parse_json(text) or {}
            if data.get("region"):
                region = str(data["region"]).strip()
                filters["region"] = region if region in BEIJING_DISTRICTS else filters["region"]
            for key in ("price_min", "price_max", "area_min", "area_max"):
                value = data.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    filters[key] = float(value)
            if data.get("rooms"):
                rooms = str(data["rooms"]).strip()
                if re.match(r"^[1-5]室", rooms):
                    filters["rooms"] = rooms[:2]
            if data.get("keyword"):
                keyword = str(data["keyword"]).strip()
                if 1 < len(keyword) <= 20:
                    filters["keyword"] = keyword
        except Exception as e:
            logger.warning(f"[Agent] 条件抽取模型调用失败，使用正则结果: {e}")

        return filters

    @staticmethod
    def _filter_note(filters: dict, houses: List[dict]) -> str:
        """把检索条件写进上下文，避免模型把「没筛到」说成「系统里没有」"""
        if not any(filters.get(k) for k in ("region", "price_min", "price_max", "rooms",
                                           "area_min", "area_max", "keyword")):
            return ""
        conds = []
        if filters.get("region"):
            conds.append(str(filters["region"]))
        if filters.get("price_max"):
            conds.append(f"总价 ≤ {filters['price_max']:g} 万")
        if filters.get("price_min"):
            conds.append(f"总价 ≥ {filters['price_min']:g} 万")
        if filters.get("rooms"):
            conds.append("或".join(as_rooms_list(filters["rooms"])))
        if filters.get("area_min"):
            conds.append(f"面积 ≥ {filters['area_min']:g} ㎡")
        if filters.get("area_max"):
            conds.append(f"面积 ≤ {filters['area_max']:g} ㎡")
        if filters.get("keyword"):
            conds.append(f"关键词「{filters['keyword']}」")
        head = "【结构化检索条件】" + "、".join(conds)
        tail = (f"命中 {len(houses)} 套" if houses else "没有命中房源，回答时要说明是条件过严，"
                                                      "不要断言系统里没有这类房子")
        return head + "；" + tail

    # ---------- 对外调用 ----------

    @staticmethod
    def _config(conversation_id: str) -> dict:
        return {"configurable": {"thread_id": conversation_id or "default"}}

    async def run(self, question: str, conversation_id: str) -> dict:
        """非流式执行，返回最终状态"""
        state = await self._graph.ainvoke(
            {"question": question, "conversation_id": conversation_id, "stream": False},
            self._config(conversation_id),
        )
        return state

    async def astream(self, question: str, conversation_id: str):
        """流式执行

        产出 (kind, payload)：
            kind="event" -> 节点内 writer 推送的事件（agent_step / retrieve_done / delta）
            kind="state" -> 每个节点执行后的完整状态（最后一个即最终结果）
        """
        async for mode, chunk in self._graph.astream(
            {"question": question, "conversation_id": conversation_id, "stream": True},
            self._config(conversation_id),
            stream_mode=["custom", "values"],
        ):
            if mode == "custom":
                yield ("event", chunk)
            else:
                yield ("state", chunk)


_rag_graph: Optional[RAGGraph] = None


def get_rag_graph() -> RAGGraph:
    global _rag_graph
    if _rag_graph is None:
        _rag_graph = RAGGraph()
    return _rag_graph


__all__ = ["RAGGraph", "RAGState", "get_rag_graph", "ROUTE_LABELS"]
