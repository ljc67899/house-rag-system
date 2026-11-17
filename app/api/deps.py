# -*- coding: utf-8 -*-
"""API 依赖注入

鉴权分三层：
    require_login      只要求 Token 有效（任意角色）
    require_admin      要求 role=admin（保留原有两个接口的兼容入口）
    require_permission 按 RBAC 权限点鉴权，是新增接口的推荐用法
"""

from typing import Callable, Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.database import get_db
from app.services.rag_service import RAGService
from app.services.house_service import HouseService
from app.services.rbac_service import get_role_permissions


def get_house_service(db: Session = Depends(get_db)) -> HouseService:
    return HouseService(db)


def get_rag_service(db: Session = Depends(get_db)) -> RAGService:
    return RAGService(db)


def _resolve_payload(authorization: Optional[str]) -> dict:
    """解析 Authorization 头，返回 Token payload（内部公共逻辑）

    未带 Token / Token 无效 / 已过期统一返回 401。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(authorization.split(" ", 1)[1].strip())
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录凭证无效或已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def require_login(authorization: Optional[str] = Header(default=None)) -> dict:
    """登录态校验：普通用户与管理员都可以通过

    与 require_admin 的区别是只要求 Token 有效，不限制角色。
    """
    return _resolve_payload(authorization)


def require_user(authorization: Optional[str] = Header(default=None)) -> dict:
    """普通用户校验：收藏 / 浏览等个性化行为必须以普通用户身份记录

    管理员存在 admin 表，其 id 与 user_info.id 不在同一空间，
    因此管理员不能写入行为数据（否则会串到 user_info 里同 id 的用户身上）。
    """
    payload = _resolve_payload(authorization)
    if payload.get("role") != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理员账号不参与收藏、浏览等个性化行为",
        )
    return payload


def optional_user(authorization: Optional[str] = Header(default=None)) -> Optional[dict]:
    """可选登录：未登录或 Token 无效时返回 None，用于房源详情这类公开接口"""
    if not authorization:
        return None
    try:
        return _resolve_payload(authorization)
    except HTTPException:
        return None


def require_admin(authorization: Optional[str] = Header(default=None)) -> dict:
    """管理员校验（保留兼容入口）

    历史接口 /user/admin/me 仍在用；内部复用 require_login 的 Token 解析逻辑，
    只额外要求 role=admin，行为与升级前一致（401）。
    新接口请优先使用 require_permission。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要管理员登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = _resolve_payload(authorization)
    if payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="管理员凭证无效或已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def require_permission(code: str) -> Callable[..., dict]:
    """权限点校验依赖工厂

    用法：
        @router.post("", dependencies=[Depends(require_permission("house:create"))])

    流程：先校验 Token（401），再按 payload["role"] 从 DB 查权限码，
    缺少目标权限返回 403「无此操作权限」。
    """

    def dependency(
        payload: dict = Depends(require_login),
        db: Session = Depends(get_db),
    ) -> dict:
        role = payload.get("role")
        permissions = get_role_permissions(db, role)
        if code not in permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无此操作权限",
            )
        return payload

    # 让 OpenAPI 里能看出这个依赖校验的是哪个权限点
    dependency.__name__ = f"require_permission_{code.replace(':', '_')}"
    return dependency
