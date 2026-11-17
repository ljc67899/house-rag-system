# -*- coding: utf-8 -*-
"""自定义异常定义"""

from typing import Any


class RAGException(Exception):
    def __init__(self, message: str, code: int = 1000, data: Any = None):
        self.message = message
        self.code = code
        self.data = data
        super().__init__(message)


class HouseNotFoundError(RAGException):
    def __init__(self, house_id: int):
        super().__init__(f"房源不存在: {house_id}", code=3001)


class UserNotFoundError(RAGException):
    def __init__(self, username: str):
        super().__init__(f"用户不存在: {username}", code=3002)


class LLMServiceError(RAGException):
    def __init__(self, message: str = "大模型服务调用失败"):
        super().__init__(message, code=2001)


class EmbeddingServiceError(RAGException):
    def __init__(self, message: str = "向量嵌入服务异常"):
        super().__init__(message, code=2002)


class VectorStoreError(RAGException):
    def __init__(self, message: str = "向量数据库操作异常"):
        super().__init__(message, code=2003)
