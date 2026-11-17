# -*- coding: utf-8 -*-
"""服务层统一出口：所有业务服务类在此集中暴露"""

from app.services.house_service import HouseService
from app.services.favorite_service import FavoriteService
from app.services.compare_service import CompareService
from app.services.recommend_service import RecommendService
from app.services.comment_service import CommentService
from app.services.browse_history_service import BrowseHistoryService
from app.services.estimate_service import EstimateService
from app.services.rag_service import RAGService
from app.services.llm_service import LLMService, get_llm_service
from app.services.embedding_service import TextEmbeddingService, get_text_embedding_service
from app.services.vector_store import VectorStoreService, get_vector_store
# LangChain / LangGraph 集成层
from app.services.langchain_adapters import (
    HouseEmbeddings,
    HouseVectorStore,
    get_house_store,
    get_doc_store,
)
from app.services.rag_graph import RAGGraph, get_rag_graph
from app.services.document_service import DocumentService, get_document_service
from app.services.seed_data import seed_sample_houses, seed_sample_users
# 大数据分析模块（A~E）
from app.services.ml_service import PricePredictionService
from app.services.collab_service import CollaborativeService
from app.services.dashboard_service import DashboardService
from app.services.sentiment_service import SentimentService
from app.services.trend_service import TrendService
from app.services.seed_simulated_data import seed_all_simulated

__all__ = [
    "HouseService",
    "FavoriteService",
    "CompareService",
    "RecommendService",
    "CommentService",
    "BrowseHistoryService",
    "EstimateService",
    "RAGService",
    "LLMService",
    "get_llm_service",
    "TextEmbeddingService",
    "get_text_embedding_service",
    "VectorStoreService",
    "get_vector_store",
    "HouseEmbeddings",
    "HouseVectorStore",
    "get_house_store",
    "get_doc_store",
    "RAGGraph",
    "get_rag_graph",
    "DocumentService",
    "get_document_service",
    "seed_sample_houses",
    "seed_sample_users",
    "PricePredictionService",
    "CollaborativeService",
    "DashboardService",
    "SentimentService",
    "TrendService",
    "seed_all_simulated",
]
