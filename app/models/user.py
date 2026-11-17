# -*- coding: utf-8 -*-
"""用户相关 SQLModel 模型"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class UserInfo(SQLModel, table=True):
    """用户信息表"""
    __tablename__ = "user_info"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: Optional[str] = Field(default=None, max_length=50, unique=True, description="用户名")
    password: Optional[str] = Field(default=None, max_length=255, description="密码")
    email: Optional[str] = Field(default=None, max_length=100, description="邮箱")
    addr: Optional[str] = Field(default=None, max_length=255, description="住址")
    collect_id: Optional[str] = Field(default=None, description="收藏房源编号")
    seen_id: Optional[str] = Field(default=None, description="浏览记录")
    create_time: Optional[datetime] = Field(default_factory=datetime.now, description="创建时间")


class Admin(SQLModel, table=True):
    """管理员表"""
    __tablename__ = "admin"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: Optional[str] = Field(default=None, max_length=50, unique=True, nullable=False, description="用户名")
    password_hash: Optional[str] = Field(default=None, max_length=255, nullable=False, description="密码哈希")
    create_time: Optional[datetime] = Field(default_factory=datetime.now, description="创建时间")
