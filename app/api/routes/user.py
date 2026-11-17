# -*- coding: utf-8 -*-
"""用户管理 API"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_login
from app.core.security import (
    TOKEN_TTL,
    create_admin_token,
    create_token,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models.schemas import (
    AdminLoginRequest,
    ChangePasswordRequest,
    UserInfoResponse,
    UserLoginRequest,
    UserRegisterRequest,
)
from app.models.user import Admin, UserInfo
from app.services.rbac_service import (
    get_role_permissions,
    hash_user_password,
    is_password_hashed,
    verify_user_password,
)

router = APIRouter(prefix="/user", tags=["用户管理"])


@router.post("/login", summary="用户登录（自动识别管理员身份）")
async def user_login(request: UserLoginRequest, db: Session = Depends(get_db)):
    """统一登录入口，登录时即确定身份

    1. 先按管理员表校验，命中则返回 role=admin 的 Token
    2. 否则按普通用户表校验，返回 role=user 的 Token

    响应中的 user.permissions 是该角色拥有的权限码列表（RBAC0），
    前端据此判断是否展示「房源管理」入口（建议判断 house:manage）；
    role 字段保持不变，老的判断逻辑仍然可用。
    """
    admin = db.query(Admin).filter(Admin.username == request.username).first()
    if admin and verify_password(request.username, request.password, admin.password_hash):
        return {
            "message": "登录成功",
            "role": "admin",
            "token": create_token(admin.id, admin.username or "", "admin"),
            "expires_in": TOKEN_TTL,
            "user": {
                "id": admin.id,
                "username": admin.username,
                "name": admin.username,
                "email": None,
                "role": "admin",
                "permissions": get_role_permissions(db, "admin"),
            },
        }

    user = db.query(UserInfo).filter(UserInfo.name == request.username).first()
    if not user or not verify_user_password(user.name or "", request.password, user.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 存量明文密码：校验通过后顺手升级为哈希（对用户透明，无需改密）
    if not is_password_hashed(user.password):
        user.password = hash_user_password(user.name or "", request.password)
        db.commit()
        db.refresh(user)

    user_data = UserInfoResponse.model_validate(user).model_dump()
    # 前端部分组件按 username 取值，这里补齐别名，避免展示 undefined
    user_data["username"] = user.name
    user_data["role"] = "user"
    user_data["permissions"] = get_role_permissions(db, "user")
    return {
        "message": "登录成功",
        "role": "user",
        "token": create_token(user.id, user.name or "", "user"),
        "expires_in": TOKEN_TTL,
        "user": user_data,
    }


@router.post("/register", summary="用户注册")
async def user_register(request: UserRegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(UserInfo).filter(UserInfo.name == request.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = UserInfo(
        name=request.username,
        password=hash_user_password(request.username, request.password),
        email=request.email,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    # 注册成功后直接下发 Token，便于前端完成自动登录与后续改密
    user_data = UserInfoResponse.model_validate(user).model_dump()
    user_data["username"] = user.name
    user_data["role"] = "user"
    user_data["permissions"] = get_role_permissions(db, "user")
    return {
        "message": "注册成功",
        "role": "user",
        "token": create_token(user.id, user.name or "", "user"),
        "expires_in": TOKEN_TTL,
        "user": user_data,
    }


# ===== 管理员登录（房源管理后台入口） =====

@router.post("/admin/login", summary="管理员登录")
async def admin_login(request: AdminLoginRequest, db: Session = Depends(get_db)):
    """管理员登录，成功后返回签名 Token

    普通用户表（user_info）与本接口无关，只有 admin 表里的账号才能登录成功，
    登录后拿到的 Token 才能调用房源增删改接口。
    """
    admin = db.query(Admin).filter(Admin.username == request.username).first()
    if not admin or not verify_password(request.username, request.password, admin.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员账号或密码错误")

    token = create_admin_token(admin.id, admin.username or "")
    return {
        "message": "管理员登录成功",
        "token": token,
        "expires_in": TOKEN_TTL,
        "admin": {"id": admin.id, "username": admin.username},
    }


@router.get("/admin/me", summary="校验管理员登录状态")
async def admin_me(payload: dict = Depends(require_admin)):
    return {
        "valid": True,
        "admin": {"id": payload.get("sub"), "username": payload.get("name")},
    }


@router.post("/change-password", summary="修改密码（管理员与普通用户通用）")
async def change_password(
    request: ChangePasswordRequest,
    payload: dict = Depends(require_login),
    db: Session = Depends(get_db),
):
    """根据 Token 中的角色，分别修改 admin 表或 user_info 表的密码

    需要先校验原密码，避免 Token 泄露后被直接改密。
    """
    if request.old_password == request.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")

    role = payload.get("role")
    subject = payload.get("sub")

    if role == "admin":
        admin = db.query(Admin).filter(Admin.id == subject).first()
        if not admin:
            raise HTTPException(status_code=404, detail="管理员账号不存在")
        if not verify_password(admin.username or "", request.old_password, admin.password_hash):
            raise HTTPException(status_code=400, detail="原密码不正确")

        admin.password_hash = hash_password(admin.username or "", request.new_password)
        db.commit()
        return {"message": "管理员密码修改成功，请重新登录", "role": "admin"}

    user = db.query(UserInfo).filter(UserInfo.id == subject).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    # 兼容存量明文密码
    if not verify_user_password(user.name or "", request.old_password, user.password):
        raise HTTPException(status_code=400, detail="原密码不正确")

    user.password = hash_user_password(user.name or "", request.new_password)
    db.commit()
    return {"message": "密码修改成功", "role": "user"}


@router.get("/{user_id}", response_model=UserInfoResponse, summary="获取用户信息")
async def get_user_info(user_id: int, db: Session = Depends(get_db)):
    user = db.query(UserInfo).filter(UserInfo.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return UserInfoResponse.model_validate(user)
