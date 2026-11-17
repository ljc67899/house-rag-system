# -*- coding: utf-8 -*-
"""数据模型统一出口：ORM 数据库模型 + Pydantic 校验模型"""

from app.models.house import (
    HouseInfo,
    HouseComment,
    HouseRecommend,
    HouseCompare,
    BrowseHistory,
)
from app.models.user import UserInfo, Admin
from app.models.trend import HousePriceTrend
from app.models.rbac import Role, Permission, RolePermission
from app.models import schemas

__all__ = [
    "HouseInfo",
    "HouseComment",
    "HouseRecommend",
    "HouseCompare",
    "BrowseHistory",
    "UserInfo",
    "Admin",
    "HousePriceTrend",
    "Role",
    "Permission",
    "RolePermission",
    "schemas",
]
