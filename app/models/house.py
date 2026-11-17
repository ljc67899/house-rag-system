# -*- coding: utf-8 -*-
"""房源相关 SQLModel 模型（对齐用户 MySQL house_info 真实表结构）"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Index, func
from sqlmodel import Field, SQLModel


class HouseInfo(SQLModel, table=True):
    """房源主表：字段与 MySQL 用户库 house_info 表严格对齐，并加地图/查询索引"""
    __tablename__ = "house_info"
    __table_args__ = (
        Index("ix_house_region_block", "region", "block"),
        Index("ix_house_region_price", "region", "price"),
        Index("ix_house_region_area", "region", "area"),
        Index("ix_house_region_lon_lat", "region", "longitude", "latitude"),
        Index("ix_house_lon_lat", "longitude", "latitude"),
        {"comment": "房源主表"},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    title: Optional[str] = Field(default=None, max_length=255, description="房源标题")
    rooms: Optional[str] = Field(default=None, max_length=50, description="户型")
    area: Optional[str] = Field(default=None, max_length=50, index=True, description="面积")
    price: Optional[str] = Field(default=None, max_length=50, index=True, description="价格")
    direction: Optional[str] = Field(default=None, max_length=50, description="朝向")
    rent_type: Optional[str] = Field(default=None, max_length=50, description="房屋类型")
    region: Optional[str] = Field(default=None, max_length=100, index=True, description="所在区域")
    block: Optional[str] = Field(default=None, max_length=100, index=True, description="所在街道/商圈")
    address: Optional[str] = Field(default=None, max_length=255, description="所在小区")
    traffic: Optional[str] = Field(default=None, description="交通条件")
    floor: Optional[str] = Field(default=None, max_length=50, description="楼层")
    build_year: Optional[str] = Field(default=None, max_length=20, description="建造年份")
    decoration: Optional[str] = Field(default=None, max_length=50, description="装修情况")
    house_type: Optional[str] = Field(default=None, max_length=50, description="房源类型")
    highlights: Optional[str] = Field(default=None, description="房屋优势")
    facilities: Optional[str] = Field(default=None, description="配套设施")
    description: Optional[str] = Field(default=None, description="房源描述")
    matching: Optional[str] = Field(default=None, description="周边配套")
    image_url: Optional[str] = Field(default=None, max_length=500, description="房源图片")
    longitude: Optional[str] = Field(default=None, max_length=50, description="经度")
    latitude: Optional[str] = Field(default=None, max_length=50, description="纬度")
    contact_name: Optional[str] = Field(default=None, max_length=50, description="业主姓名")
    contact_phone: Optional[str] = Field(default=None, max_length=50, description="业主电话")
    publish_date: Optional[str] = Field(default=None, max_length=20, description="发布日期")
    created_at: Optional[datetime] = Field(
        default_factory=datetime.now,
        sa_column_kwargs={"server_default": func.now()},
        description="记录创建时间",
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"onupdate": func.now()},
        description="记录更新时间",
    )

    def to_dict(self):
        return {
            "id": self.id, "title": self.title, "rooms": self.rooms,
            "area": self.area, "price": self.price, "direction": self.direction,
            "rent_type": self.rent_type, "region": self.region, "block": self.block,
            "address": self.address, "traffic": self.traffic, "floor": self.floor,
            "build_year": self.build_year, "decoration": self.decoration,
            "house_type": self.house_type, "description": self.description,
            "highlights": self.highlights, "facilities": self.facilities,
            "matching": self.matching, "image_url": self.image_url,
            "longitude": self.longitude, "latitude": self.latitude,
            "contact_name": self.contact_name, "contact_phone": self.contact_phone,
            "publish_date": self.publish_date,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_search_doc(self) -> str:
        """生成用于 RAG 检索的文档文本"""
        parts = []
        if self.title: parts.append(f"房源标题：{self.title}")
        if self.address: parts.append(f"小区：{self.address}")
        if self.region: parts.append(f"区域：{self.region}")
        if self.block: parts.append(f"街道：{self.block}")
        if self.price: parts.append(f"价格：{self.price}")
        if self.rooms: parts.append(f"户型：{self.rooms}")
        if self.area: parts.append(f"面积：{self.area}")
        if self.direction: parts.append(f"朝向：{self.direction}")
        if self.rent_type: parts.append(f"房屋类型：{self.rent_type}")
        if self.traffic: parts.append(f"交通：{self.traffic}")
        if self.facilities: parts.append(f"配套设施：{self.facilities}")
        if self.highlights: parts.append(f"房屋优势：{self.highlights}")
        if self.matching: parts.append(f"周边配套：{self.matching}")
        return "；".join(parts)


class HouseComment(SQLModel, table=True):
    """房源评论表（情感分析数据源）"""
    __tablename__ = "house_comment"

    id: Optional[int] = Field(default=None, primary_key=True)
    house_id: Optional[int] = Field(default=None, index=True, description="房源ID")
    user_id: Optional[int] = Field(default=None, description="用户ID")
    user_name: Optional[str] = Field(default=None, max_length=50, description="用户名")
    score: Optional[float] = Field(default=None, description="评分")
    content: Optional[str] = Field(default=None, description="评论内容")
    sentiment: Optional[str] = Field(default=None, max_length=20, description="情感标签")
    create_time: Optional[datetime] = Field(default_factory=datetime.now, description="评论时间")


class HouseCommentReply(SQLModel, table=True):
    """评论回复表（单层楼中楼：回复挂在评论下，reply_to 记录被回复人昵称）"""
    __tablename__ = "house_comment_reply"

    id: Optional[int] = Field(default=None, primary_key=True)
    comment_id: Optional[int] = Field(default=None, index=True, description="所属评论ID")
    house_id: Optional[int] = Field(default=None, index=True, description="房源ID")
    user_id: Optional[int] = Field(default=None, description="回复人ID")
    user_name: Optional[str] = Field(default=None, max_length=50, description="回复人昵称")
    reply_to: Optional[str] = Field(default=None, max_length=50, description="被回复人昵称")
    content: Optional[str] = Field(default=None, description="回复内容")
    create_time: Optional[datetime] = Field(default_factory=datetime.now, description="回复时间")


class HouseRecommend(SQLModel, table=True):
    """用户-房源推荐记录表"""
    __tablename__ = "house_recommend"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, index=True, description="用户ID")
    house_id: Optional[int] = Field(default=None, description="房源ID")
    title: Optional[str] = Field(default=None, max_length=255, description="房源标题快照")
    address: Optional[str] = Field(default=None, max_length=255, description="小区快照")
    block: Optional[str] = Field(default=None, max_length=50, description="街道快照")
    score: Optional[int] = Field(default=None, description="推荐得分")


class HouseCompare(SQLModel, table=True):
    """用户房源对比记录表"""
    __tablename__ = "house_compare"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_name: Optional[str] = Field(default=None, max_length=50, description="用户名")
    house_id: Optional[int] = Field(default=None, description="房源ID")
    add_time: Optional[datetime] = Field(default_factory=datetime.now, description="添加时间")


class BrowseHistory(SQLModel, table=True):
    """浏览历史表"""
    __tablename__ = "browse_history"
    __table_args__ = (
        Index("ix_history_user_house", "user_id", "house_id"),
        {"comment": "用户浏览历史"},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, index=True, description="用户ID")
    house_id: Optional[int] = Field(default=None, index=True, description="房源ID")
    house_title: Optional[str] = Field(default=None, max_length=255, description="房源标题快照")
    house_price: Optional[str] = Field(default=None, max_length=50, description="房源价格快照")
    house_address: Optional[str] = Field(default=None, max_length=255, description="小区快照")
    view_time: Optional[datetime] = Field(default_factory=datetime.now, description="浏览时间")
