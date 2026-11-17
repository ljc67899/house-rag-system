# -*- coding: utf-8 -*-
"""RBAC0 权限服务

职责：
    1. seed_rbac：幂等初始化角色、权限点与角色-权限映射（存在则跳过 / 补空，不重复插入）
    2. get_role_permissions：按角色编码查询权限码列表（进程内缓存，避免每次请求查库）
    3. invalidate_cache：角色权限变更后清理缓存
    4. 普通用户密码工具：兼容「存量明文 + 新哈希」两种存储形态

权限点固定为 7 个：
    house:read / house:manage / house:create / house:update / house:delete /
    user:change-password / knowledge:manage
角色映射：
    admin = 全部；user = house:read + user:change-password
"""

import hmac
import re
import threading
from typing import Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.rbac import Permission, Role, RolePermission

# ===== 权限点定义 =====
PERMISSION_DEFINITIONS: List[Tuple[str, str, str]] = [
    ("house:read", "查看房源", "查看房源列表与详情"),
    ("house:manage", "房源管理入口", "进入房源管理后台的权限标识"),
    ("house:create", "新增房源", "创建房源"),
    ("house:update", "更新房源", "修改房源信息"),
    ("house:delete", "删除房源", "删除房源"),
    ("user:change-password", "修改密码", "修改本人登录密码"),
    ("knowledge:manage", "知识库管理", "上传 / 删除知识文档、重建房源向量索引"),
    ("user:manage", "用户管理", "查看用户列表、重置密码、删除用户"),
    ("comment:manage", "评论管理", "查看与删除房源评论"),
]

ALL_PERMISSION_CODES: List[str] = [code for code, _, _ in PERMISSION_DEFINITIONS]

# ===== 角色定义 =====
ROLE_DEFINITIONS: List[Tuple[str, str, str]] = [
    ("admin", "管理员", "拥有全部权限，可管理房源与用户"),
    ("user", "普通用户", "可浏览房源、修改本人密码"),
]

# ===== 角色 -> 权限映射 =====
ROLE_PERMISSIONS: Dict[str, List[str]] = {
    "admin": list(ALL_PERMISSION_CODES),
    "user": ["house:read", "user:change-password"],
}

# 进程内缓存：{role_code: [permission_code, ...]}
_permission_cache: Dict[str, List[str]] = {}
_cache_lock = threading.Lock()


def invalidate_cache(role_code: Optional[str] = None) -> None:
    """清理权限缓存

    role_code 为空时清空全部；否则只清理指定角色。
    """
    with _cache_lock:
        if role_code is None:
            _permission_cache.clear()
        else:
            _permission_cache.pop(role_code, None)


def seed_rbac(db: Session) -> dict:
    """幂等初始化 RBAC 数据

    可重复调用：已存在的角色 / 权限 / 映射不会被重复插入，
    只在缺失时补齐，因此服务每次启动执行都是安全的。
    """
    stats = {"roles": 0, "permissions": 0, "mappings": 0}

    # ---------- 1. 角色 ----------
    existing_roles = {r.code: r for r in db.query(Role).all()}
    for code, name, description in ROLE_DEFINITIONS:
        role = existing_roles.get(code)
        if role is None:
            role = Role(code=code, name=name, description=description)
            db.add(role)
            existing_roles[code] = role
            stats["roles"] += 1
        else:
            # 只补空值，不覆盖人工维护的内容
            if not role.name:
                role.name = name
            if not role.description:
                role.description = description

    # ---------- 2. 权限点 ----------
    existing_permissions = {p.code: p for p in db.query(Permission).all()}
    for code, name, description in PERMISSION_DEFINITIONS:
        permission = existing_permissions.get(code)
        if permission is None:
            permission = Permission(code=code, name=name, description=description)
            db.add(permission)
            existing_permissions[code] = permission
            stats["permissions"] += 1
        else:
            if not permission.name:
                permission.name = name
            if not permission.description:
                permission.description = description

    # 拿到自增主键，便于后面写关联表
    db.flush()

    # ---------- 3. 角色-权限映射 ----------
    existing_pairs = {
        (rp.role_id, rp.permission_id) for rp in db.query(RolePermission).all()
    }
    for role_code, permission_codes in ROLE_PERMISSIONS.items():
        role = existing_roles.get(role_code)
        if role is None or role.id is None:
            continue
        for code in permission_codes:
            permission = existing_permissions.get(code)
            if permission is None or permission.id is None:
                continue
            pair = (role.id, permission.id)
            if pair in existing_pairs:
                continue
            db.add(RolePermission(role_id=role.id, permission_id=permission.id))
            existing_pairs.add(pair)
            stats["mappings"] += 1

    db.commit()
    invalidate_cache()
    if any(stats.values()):
        logger.info(
            f"RBAC 初始化完成: 新增角色 {stats['roles']}、权限 {stats['permissions']}、"
            f"映射 {stats['mappings']}"
        )
    return stats


def get_role_permissions(db: Session, role_code: Optional[str]) -> List[str]:
    """查询角色拥有的权限码列表

    命中进程内缓存直接返回；查库异常时按「无权限」处理（失败即拒绝），
    避免鉴权环节把 500 抛给前端。
    """
    if not role_code:
        return []

    with _cache_lock:
        cached = _permission_cache.get(role_code)
    if cached is not None:
        return list(cached)

    try:
        rows = (
            db.query(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .filter(Role.code == role_code)
            .all()
        )
        permissions = sorted({row[0] for row in rows if row[0]})
    except Exception as e:  # 表不存在 / 数据库不可用时降级为「无权限」
        logger.warning(f"查询角色权限失败(role={role_code}): {e}")
        return []

    with _cache_lock:
        _permission_cache[role_code] = permissions
    return list(permissions)


# ===== 普通用户密码工具（兼容存量明文） =====

_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def is_password_hashed(stored: Optional[str]) -> bool:
    """判断存储串是否已是哈希（64 位十六进制）"""
    if not stored:
        return False
    return bool(_HASH_PATTERN.match(stored))


def hash_user_password(username: str, raw_password: str) -> str:
    """普通用户密码哈希（与管理员同一套 sha256 方案）"""
    return hash_password(username, raw_password)


def verify_user_password(username: str, raw_password: str, stored: Optional[str]) -> bool:
    """校验普通用户密码，兼容存量明文

    已哈希 -> verify_password；否则按明文直接比对。
    """
    if not stored or raw_password is None:
        return False
    if is_password_hashed(stored):
        return verify_password(username, raw_password, stored)
    # 存量明文：定长比较，避免时序攻击
    # 注意：compare_digest 对含非 ASCII 字符的 str 会抛 TypeError（中文密码），统一编码成 bytes 再比较
    return hmac.compare_digest(stored.encode("utf-8"), raw_password.encode("utf-8"))


__all__ = [
    "PERMISSION_DEFINITIONS",
    "ALL_PERMISSION_CODES",
    "ROLE_DEFINITIONS",
    "ROLE_PERMISSIONS",
    "seed_rbac",
    "get_role_permissions",
    "invalidate_cache",
    "is_password_hashed",
    "hash_user_password",
    "verify_user_password",
]
