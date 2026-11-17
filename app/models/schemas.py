# -*- coding: utf-8 -*-
"""Pydantic 数据模型定义"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class ApiResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Optional[dict] = None


class HouseBase(BaseModel):
    title: Optional[str] = None
    rooms: Optional[str] = None
    area: Optional[str] = None
    price: Optional[str] = None
    direction: Optional[str] = None
    rent_type: Optional[str] = None
    region: Optional[str] = None
    block: Optional[str] = None
    address: Optional[str] = None
    traffic: Optional[str] = None
    floor: Optional[str] = None
    build_year: Optional[str] = None
    decoration: Optional[str] = None
    house_type: Optional[str] = None
    highlights: Optional[str] = None
    facilities: Optional[str] = None
    description: Optional[str] = None
    matching: Optional[str] = None
    image_url: Optional[str] = None
    longitude: Optional[str] = None
    latitude: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    publish_date: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    """修改密码请求"""
    old_password: str = Field(..., min_length=1, max_length=100)
    new_password: str = Field(..., min_length=6, max_length=100, description="新密码至少 6 位")


class AdminLoginRequest(BaseModel):
    """管理员登录请求"""
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=100)


class HouseCreateRequest(HouseBase):
    """新增房源：标题、区域、价格必填，其余可选"""
    title: str = Field(..., min_length=1, max_length=255)
    region: str = Field(..., min_length=1, max_length=100)
    price: str = Field(..., min_length=1, max_length=50)


class HouseUpdateRequest(HouseBase):
    """更新房源：所有字段可选，仅更新传入（非 None）的字段"""
    pass


class HouseInfo(HouseBase):
    id: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    class Config:
        from_attributes = True


class HouseListResponse(BaseModel):
    total: int
    houses: List[HouseInfo]
    page: int
    page_size: int
    total_pages: int


class HouseSearchRequest(BaseModel):
    keyword: Optional[str] = None
    region: Optional[str] = None
    block: Optional[str] = None
    rooms: Optional[str] = None
    min_price: Optional[str] = None
    max_price: Optional[str] = None
    page: int = 1
    page_size: int = 10


class UserLoginRequest(BaseModel):
    username: str
    password: str


class UserRegisterRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None


class UserInfoResponse(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    addr: Optional[str] = None
    class Config:
        from_attributes = True


class SourceReference(BaseModel):
    source_id: str
    source_type: str
    title: str
    content_preview: str
    score: float
    house_id: Optional[int] = None
    document_name: Optional[str] = None


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    conversation_id: Optional[str] = None
    user_id: Optional[int] = None
    top_k: Optional[int] = None


class ChatResponse(BaseModel):
    answer: str
    conversation_id: str
    sources: List[SourceReference]
    model: str
    latency: float
    related_houses: List[HouseInfo] = []


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str
    database_connected: bool = False
    llm_connected: bool = False
    vector_db_connected: bool = False


# ===== 收藏相关 =====
class FavoriteBase(BaseModel):
    house_id: int

class FavoriteCreate(FavoriteBase):
    user_id: int = 0

class FavoriteResponse(BaseModel):
    id: int
    house_id: int
    house_title: str
    house_price: str
    house_address: str
    add_time: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class FavoriteListResponse(BaseModel):
    favorites: list
    total: int

# ===== 对比相关 =====
class CompareAdd(BaseModel):
    user_name: str = "guest"
    house_id: int

class CompareItem(BaseModel):
    id: int
    house_id: int
    house_title: str
    house_price: str
    rooms: str
    area: str
    direction: str
    address: str

    model_config = ConfigDict(from_attributes=True)

class CompareListResponse(BaseModel):
    items: list
    total: int

# ===== 推荐相关 =====
class RecommendItem(BaseModel):
    house_id: int
    title: str
    address: str
    score: float
    reason: str

    model_config = ConfigDict(from_attributes=True)

class RecommendResponse(BaseModel):
    recommendations: list
    total: int

# ===== 评论相关 =====
class CommentCreate(BaseModel):
    house_id: int
    user_id: int = 0
    user_name: str = "匿名用户"
    score: float = 5.0
    content: str

class CommentResponse(BaseModel):
    id: int
    house_id: int
    user_id: int
    user_name: str
    score: float
    content: str
    create_time: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class CommentListResponse(BaseModel):
    comments: list
    total: int
    avg_score: float = 0.0

# ===== 浏览历史 =====
class BrowseHistoryItem(BaseModel):
    id: int
    house_id: int
    house_title: str
    house_price: str
    house_address: str
    view_time: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class BrowseHistoryResponse(BaseModel):
    history: list
    total: int

# ===== 租金估算 =====
class RentEstimateRequest(BaseModel):
    rooms: str = "2室1厅"
    area: float = 80.0
    region: str = "朝阳区"
    direction: str = "南北"
    decoration: str = "精装修"
    floor: str = "中层"

class RentEstimateResponse(BaseModel):
    estimated_rent_min: float
    estimated_rent_max: float
    estimated_rent_avg: float
    confidence: float
    factors: dict
    similar_houses_count: int
    advice: str

# ===== 模块 A：房价智能预测（深度学习回归） =====
class MLPredictRequest(BaseModel):
    region: str = "朝阳区"
    area: float = 88.0
    rooms: str = "2室1厅1卫"
    direction: str = "朝南"
    rent_type: str = "住宅"

# ===== 模块 D：评论情感分析 =====
class SentimentRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)

# ===== 模块 B：协同过滤推荐 =====
class CollabRecommendRequest(BaseModel):
    user_id: int
    limit: int = 10
