# -*- coding: utf-8 -*-
"""鉴权工具：管理员密码哈希与签名 Token

不引入额外依赖，用标准库 hmac + sha256 实现无状态 Token：
    payload = {"sub": 管理员id, "name": 用户名, "role": "admin", "exp": 过期时间}
    token   = base64url(payload) + "." + HMAC-SHA256 签名

选择签名 Token 而不是数据库 session 表的原因：
    1. 服务重启后依然有效，不需要额外的会话存储
    2. 无状态，天然支持多进程部署
    3. 密钥复用 settings.secret_key，无需新增配置
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Optional

from app.config import settings

# 管理端登录有效期（秒），默认 12 小时
TOKEN_TTL = 12 * 3600


def hash_password(username: str, password: str) -> str:
    """管理员密码哈希

    把用户名作为盐拼进去，避免「相同密码 -> 相同哈希」被直接看出来。
    """
    return hashlib.sha256(f"{username}:{password}".encode("utf-8")).hexdigest()


def verify_password(username: str, password: str, password_hash: str) -> bool:
    """校验管理员密码（使用 compare_digest 防时序攻击）"""
    return hmac.compare_digest(hash_password(username, password), password_hash or "")


def _sign(raw: str) -> str:
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_token(subject: int, name: str, role: str, expires_in: int = TOKEN_TTL) -> str:
    """签发 Token，role 用于区分 admin / user"""
    payload = {
        "sub": subject,
        "name": name,
        "role": role,
        "exp": int(time.time()) + expires_in,
    }
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("utf-8").rstrip("=")
    return f"{raw}.{_sign(raw)}"


def create_admin_token(admin_id: int, username: str, expires_in: int = TOKEN_TTL) -> str:
    """签发管理员 Token（role=admin）"""
    return create_token(admin_id, username, "admin", expires_in)


def decode_token(token: str) -> Optional[dict]:
    """解析并校验 Token（签名错误或已过期返回 None）"""
    if not token or "." not in token:
        return None

    raw, _, signature = token.rpartition(".")
    if not hmac.compare_digest(signature, _sign(raw)):
        return None

    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
    except Exception:
        return None

    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def decode_admin_token(token: str) -> Optional[dict]:
    """解析管理员 Token（额外要求 role=admin）"""
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        return None
    return payload


__all__ = [
    "TOKEN_TTL",
    "hash_password",
    "verify_password",
    "create_token",
    "create_admin_token",
    "decode_token",
    "decode_admin_token",
]
