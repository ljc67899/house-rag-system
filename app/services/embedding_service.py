# -*- coding: utf-8 -*-
"""向量嵌入服务（API 方式为主）"""

import os
from typing import List, Optional
from loguru import logger
import numpy as np
from app.config import settings
from app.core.exceptions import EmbeddingServiceError


class TextEmbeddingService:
    def __init__(self):
        self.embedding_type = settings.embedding_type
        self.api_model = settings.embedding_api_model
        self.model_name = settings.embedding_model_name
        self.device = settings.embedding_device
        self.batch_size = settings.embedding_batch_size
        self._model = None
        self._model_dim = None
        self._api_client = None
        self._api_dim = 1536
        if settings.hf_mirror:
            os.environ["HF_ENDPOINT"] = settings.hf_mirror

    def _lazy_load(self):
        if self.embedding_type == "api":
            self._lazy_load_api()
        else:
            self._lazy_load_local()

    def _lazy_load_api(self):
        if self._api_client is None:
            try:
                import openai
                self._api_client = openai.OpenAI(
                    api_key=settings.llm_api_key,
                    base_url=settings.llm_api_base,
                )
                logger.info(f"Text embedding API client initialized, model={self.api_model}")
            except Exception as e:
                logger.error(f"Failed to init embedding API client: {e}")
                raise EmbeddingServiceError(f"嵌入API客户端初始化失败: {e}")

    def _lazy_load_local(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                logger.info(f"Loading text embedding model: {self.model_name}")
                self._model = SentenceTransformer(self.model_name, device=self.device)
                test_emb = self._model.encode(["test"])
                self._model_dim = test_emb.shape[1]
                logger.info(f"Text embedding model loaded, dim={self._model_dim}")
            except Exception as e:
                logger.error(f"Failed to load text embedding model: {e}")
                raise EmbeddingServiceError(f"文本嵌入模型加载失败: {e}")

    @property
    def dimension(self) -> int:
        if self.embedding_type == "api":
            return self._api_dim
        self._lazy_load()
        return self._model_dim

    def encode(self, texts: List[str]) -> List[List[float]]:
        if self.embedding_type == "api":
            return self._encode_api(texts)
        return self._encode_local(texts)

    def _encode_api(self, texts: List[str]) -> List[List[float]]:
        self._lazy_load()
        try:
            response = self._api_client.embeddings.create(
                model=self.api_model, input=texts,
            )
            embeddings = [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
            emb_array = np.array(embeddings)
            norms = np.linalg.norm(emb_array, axis=1, keepdims=True)
            norms[norms == 0] = 1
            emb_array = emb_array / norms
            return emb_array.tolist()
        except Exception as e:
            logger.error(f"API embedding failed: {e}")
            raise EmbeddingServiceError(f"嵌入API调用失败: {e}")

    def _encode_local(self, texts: List[str]) -> List[List[float]]:
        self._lazy_load()
        try:
            embeddings = self._model.encode(
                texts, batch_size=self.batch_size,
                show_progress_bar=False, normalize_embeddings=True,
            )
            return embeddings.tolist()
        except Exception as e:
            logger.error(f"Text embedding failed: {e}")
            raise EmbeddingServiceError(f"文本嵌入失败: {e}")

    def encode_query(self, query: str) -> List[float]:
        if self.embedding_type == "local" and "bge" in self.model_name.lower():
            query = f"为这个句子生成表示以用于检索相关文章：{query}"
        return self.encode([query])[0]


_text_embedding_service: Optional[TextEmbeddingService] = None

def get_text_embedding_service() -> TextEmbeddingService:
    global _text_embedding_service
    if _text_embedding_service is None:
        _text_embedding_service = TextEmbeddingService()
    return _text_embedding_service
