# -*- coding: utf-8 -*-
"""RBAC0 权限模型（角色 / 权限 / 角色-权限映射）

设计原则：
    1. 只新增表，不修改已有表（user_info、admin 等）的字段，避免 MySQL 存量表结构冲突；
    2. 角色与权限之间是多对多，用 role_permission 关联表并加唯一约束，防止重复授权；
    3. 表名统一加 rbac_ 前缀，与业务表区分开。
"""

from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class Role(SQLModel, table=True):
    """角色表"""
    __tablename__ = "rbac_role"

    id: Optional[int] = Field(default=None, primary_key=True)
    code: Optional[str] = Field(
        default=None, max_length=50, unique=True, index=True, description="角色编码，如 admin / user"
    )
    name: Optional[str] = Field(default=None, max_length=50, description="角色名称")
    description: Optional[str] = Field(default=None, max_length=255, description="角色说明")


class Permission(SQLModel, table=True):
    """权限点表"""
    __tablename__ = "rbac_permission"

    id: Optional[int] = Field(default=None, primary_key=True)
    code: Optional[str] = Field(
        default=None, max_length=64, unique=True, index=True, description="权限编码，如 house:create"
    )
    name: Optional[str] = Field(default=None, max_length=50, description="权限名称")
    description: Optional[str] = Field(default=None, max_length=255, description="权限说明")


class RolePermission(SQLModel, table=True):
    """角色-权限关联表"""
    __tablename__ = "rbac_role_permission"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_rbac_role_permission"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    role_id: Optional[int] = Field(default=None, index=True, description="角色ID")
    permission_id: Optional[int] = Field(default=None, index=True, description="权限ID")
