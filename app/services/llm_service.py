# -*- coding: utf-8 -*-
"""LLM 大语言模型服务（LangChain 实现）

改造说明：
    旧实现：直接使用 openai.AsyncOpenAI 手写 chat.completions 调用
    新实现：langchain_openai.ChatOpenAI + ChatPromptTemplate + LCEL
    收益：Prompt 结构化可复用、模型可无缝替换、可被 LangGraph 节点直接调用

对外方法签名保持与原实现一致，上层无需改动。
"""

from typing import AsyncGenerator, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger

from app.config import settings
from app.core.exceptions import LLMServiceError


# ============ Prompt 模板（集中管理，便于调优与复用） ============

RAG_SYSTEM_PROMPT = """你是安居乐房产智能助手，一位专业的房产顾问。
回答要求：
1. 只依据提供的房源信息和知识作答，不要编造价格、面积等数据
2. 推荐房源时说明推荐理由，并给出关键信息（小区、价格、户型、面积）
3. 回答清晰有条理，使用中文
4. 如果检索到的信息不足以回答，明确说明并给出建议
5. 房源库只覆盖北京：用户问北京以外的城市时，直接说明未收录该城市房源，禁止用北京房源冒充或改写城市名
6. 回答中提到的每个小区/价格都必须能在上下文里找到出处"""

RAG_USER_PROMPT = """用户问题：{question}

{context}

请根据以上信息回答用户问题："""

GENERAL_SYSTEM_PROMPT = """你是安居乐房产智能助手，擅长回答房产相关问题，也可以友好地回应问候、介绍自己或进行日常交流。
回答要求：
1. 保持礼貌、热情、简洁
2. 对于房产问题，尽量基于常识回答；若需要具体房源信息，提示用户可以描述需求
3. 用中文回答"""

# LangChain Prompt 模板
RAG_PROMPT = ChatPromptTemplate.from_messages([
    ("system", RAG_SYSTEM_PROMPT),
    ("human", RAG_USER_PROMPT),
])


def build_context(context_houses: Optional[List[dict]], context_docs: Optional[List[str]]) -> str:
    """把检索到的房源与知识文档拼装成上下文文本"""
    parts = []

    if context_houses:
        lines = ["【相关房源】"]
        for i, house in enumerate(context_houses, 1):
            lines.append(f"房源{i}: {house.get('title', '')}")
            lines.append(f"  - 小区: {house.get('address', '')}")
            lines.append(f"  - 价格: {house.get('price', '')}")
            lines.append(f"  - 户型: {house.get('rooms', '')}")
            lines.append(f"  - 面积: {house.get('area', '')}")
            lines.append(f"  - 优势: {house.get('highlights', '')}")
            lines.append("")
        parts.append("\n".join(lines))

    if context_docs:
        lines = ["【相关知识】"]
        for i, doc in enumerate(context_docs, 1):
            lines.append(f"资料{i}: {doc}")
            lines.append("")
        parts.append("\n".join(lines))

    return "\n".join(parts)


class LLMService:
    """基于 LangChain ChatOpenAI 的大模型服务"""

    def __init__(self):
        self.model_name = settings.llm_model_name
        self.temperature = settings.llm_temperature
        self.max_tokens = settings.llm_max_tokens

        self._llm = ChatOpenAI(
            model=self.model_name,
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        logger.info(f"LLM service initialized (LangChain ChatOpenAI): {self.model_name}")

    @property
    def llm(self) -> ChatOpenAI:
        """暴露底层 ChatModel，供 LangGraph / LCEL 使用"""
        return self._llm

    def as_rag_chain(self):
        """LCEL 链：Prompt 模板 | ChatModel | 字符串解析器"""
        return RAG_PROMPT | self._llm | StrOutputParser()

    # ---------- 基础调用 ----------

    @staticmethod
    def _build_messages(prompt: str, system_prompt: Optional[str] = None):
        messages = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))
        return messages

    @staticmethod
    def _chunk_text(chunk) -> str:
        """兼容不同模型返回的 content 结构（str 或 content blocks）"""
        content = getattr(chunk, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return ""

    @staticmethod
    def _message_text(message) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return str(content)

    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        try:
            result = await self._llm.ainvoke(self._build_messages(prompt, system_prompt))
            logger.info("LLM generate success")
            return self._message_text(result)
        except Exception as e:
            logger.error(f"LLM generate failed: {e}")
            raise LLMServiceError(f"大模型调用失败: {str(e)}")

    async def generate_stream(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        try:
            async for chunk in self._llm.astream(self._build_messages(prompt, system_prompt)):
                text = self._chunk_text(chunk)
                if text:
                    yield text
        except Exception as e:
            logger.error(f"LLM stream failed: {e}")
            raise LLMServiceError(f"大模型流式调用失败: {str(e)}")

    # ---------- RAG 生成 ----------

    def format_rag_messages(self, question: str, context_houses, context_docs):
        """用 ChatPromptTemplate 生成结构化消息"""
        return RAG_PROMPT.format_messages(
            question=question,
            context=build_context(context_houses, context_docs),
        )

    async def rag_generate(self, question: str, context_houses: List[dict], context_docs: List[str]) -> str:
        try:
            messages = self.format_rag_messages(question, context_houses, context_docs)
            result = await self._llm.ainvoke(messages)
            return self._message_text(result)
        except Exception as e:
            logger.error(f"RAG generate failed: {e}")
            raise LLMServiceError(f"RAG 生成失败: {str(e)}")

    async def rag_generate_stream(
        self, question: str, context_houses: List[dict], context_docs: List[str]
    ) -> AsyncGenerator[str, None]:
        try:
            messages = self.format_rag_messages(question, context_houses, context_docs)
            async for chunk in self._llm.astream(messages):
                text = self._chunk_text(chunk)
                if text:
                    yield text
        except Exception as e:
            logger.error(f"RAG stream failed: {e}")
            raise LLMServiceError(f"RAG 流式生成失败: {str(e)}")


_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service


__all__ = [
    "LLMService",
    "get_llm_service",
    "build_context",
    "RAG_PROMPT",
    "RAG_SYSTEM_PROMPT",
    "GENERAL_SYSTEM_PROMPT",
]
