"""
NexusVideo Backend - 认证路由
============================================================
架构位置：routers/auth.py
被 local_server.py 注册为 /api/v1/auth/* 路由。

端点清单：
  POST /api/v1/auth/register  — 手机号注册
  POST /api/v1/auth/login     — 手机号登录
  POST /api/v1/auth/refresh   — Token 刷新
  GET  /api/v1/auth/me        — 获取当前用户信息（含额度）
  GET  /api/v1/auth/quota     — 查询剩余额度

设计要点：
  - MVP 阶段使用手机号 + 密码认证（短信验证码预留接口）
  - Token 有效期 24 小时，refresh 端点可延长
  - 所有端点返回统一的 {success, data, error_code, message} 结构
  - 注册/登录时自动生成 JWT Token，前端存储后使用
"""

import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from loguru import logger

from core.auth import (
    create_access_token,
    get_current_user,
    validate_phone,
    ROLE_FREE,
    ROLE_PAID,
    QUOTA_PER_ROLE,
    USER_NOT_FOUND,
)
from core.user_db import (
    ensure_db_ready,
    register_user,
    login_verify,
    get_user,
    check_quota,
)
from exceptions import NexusError

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])


# ================================================================
# 请求模型
# ================================================================
class PhonePasswordRequest(BaseModel):
    """注册 / 登录通用请求体。"""
    phone: str = Field(
        ...,
        description="手机号（11 位中国大陆手机号）",
        examples=["13812345678"],
    )
    password: str = Field(
        ...,
        min_length=6,
        max_length=64,
        description="密码（6-64 位，建议包含字母 + 数字）",
        examples=["Nexus2026!"],
    )

    @field_validator("phone")
    @classmethod
    def phone_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("手机号不能为空")
        return v.strip()

    @field_validator("password")
    @classmethod
    def password_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("密码不能为空")
        return v.strip()


class RefreshRequest(BaseModel):
    """Token 刷新请求体。"""
    access_token: str = Field(
        ...,
        description="当前有效的 Access Token（用于签发新 Token）",
    )


# ================================================================
# 响应模型
# ================================================================
class AuthResponse(BaseModel):
    """认证操作通用响应。"""
    success: bool = Field(..., description="操作是否成功")
    error_code: Optional[str] = Field(None, description="错误码（成功时为空）")
    message: str = Field(..., description="提示信息")


class TokenResponse(BaseModel):
    """注册 / 登录 / 刷新返回的 Token 响应。"""
    success: bool = Field(default=True, description="操作是否成功")
    error_code: Optional[str] = Field(None, description="错误码")
    message: str = Field(..., description="提示信息")
    data: dict = Field(..., description="用户信息 + Token")


class UserResponse(BaseModel):
    """当前用户信息响应。"""
    success: bool = Field(default=True)
    error_code: Optional[str] = Field(None)
    message: str = Field(default="获取成功")
    data: dict = Field(..., description="用户详细信息")


# ================================================================
# 端点实现
# ================================================================
@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="用户注册",
    description=(
        "手机号 + 密码注册。返回 JWT Access Token。\n\n"
        "注册后角色默认为 free（每日 5 次生成额度）。\n"
        "生产环境需增加短信验证码校验。"
    ),
)
async def register(request: PhonePasswordRequest) -> TokenResponse:
    """
    用户注册。

    流程：
        1. 校验手机号格式（自动去除 +86、空格、连字符）
        2. 检查手机号是否已注册
        3. bcrypt 哈希密码
        4. 写入 SQLite
        5. 生成 JWT Token（24h 有效期）
        6. 返回 Token + 用户信息
    """
    ensure_db_ready()

    # 校验手机号（格式校验 + 标准化）
    phone = validate_phone(request.phone)

    try:
        # 创建用户
        user = register_user(phone, request.password)

        # 生成 Token
        token = create_access_token(str(user["id"]))

        return TokenResponse(
            success=True,
            message="注册成功",
            data={
                "token": token,
                "token_type": "bearer",
                "expires_in": 86400,
                "user": {
                    "id": user["id"],
                    "phone": user["phone"],
                    "role": user["role"],
                    "quota_daily": user["quota_daily"],
                    "used_today": user["used_today"],
                    "remaining": user["quota_daily"] - user["used_today"],
                    "created_at": user["created_at"],
                },
            },
        )
    except NexusError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={
                "success": False,
                "error_code": e.error_code,
                "message": e.message,
            },
        )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="用户登录",
    description=(
        "手机号 + 密码登录。返回 JWT Access Token。\n\n"
        "登录成功后自动重置当日额度（若已过零点）。"
    ),
)
async def login(request: PhonePasswordRequest) -> TokenResponse:
    """
    用户登录。

    流程：
        1. 校验手机号格式
        2. 查询用户记录
        3. bcrypt 校验密码
        4. 自动重置每日额度（若跨天）
        5. 生成 JWT Token
        6. 返回 Token + 用户信息
    """
    ensure_db_ready()

    phone = validate_phone(request.phone)

    try:
        user, _ = login_verify(phone, request.password)
        token = create_access_token(str(user["id"]))
        remaining = max(0, user["quota_daily"] - user["used_today"])

        return TokenResponse(
            success=True,
            message="登录成功",
            data={
                "token": token,
                "token_type": "bearer",
                "expires_in": 86400,
                "user": {
                    "id": user["id"],
                    "phone": user["phone"],
                    "role": user["role"],
                    "quota_daily": user["quota_daily"],
                    "used_today": user["used_today"],
                    "remaining": remaining,
                    "last_reset": user["last_reset"],
                },
            },
        )
    except NexusError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={
                "success": False,
                "error_code": e.error_code,
                "message": e.message,
            },
        )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Token 刷新",
    description=(
        "使用当前有效的 Access Token 换取一个新的 Access Token。\n"
        "用于 Token 即将过期时的无感续期。"
    ),
)
async def refresh_token(request: RefreshRequest) -> TokenResponse:
    """
    Token 刷新。

    流程：
        1. 验证旧 Token 有效性（含过期时间）
        2. 提取 user_id
        3. 生成新的 Token（24h）
        4. 返回新 Token + 用户信息
    """
    from core.auth import verify_token

    try:
        payload = verify_token(request.access_token)
    except NexusError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={
                "success": False,
                "error_code": e.error_code,
                "message": e.message,
            },
        )

    user_id = payload["user_id"]
    new_token = create_access_token(str(user_id))

    # 获取用户最新信息
    user = get_user(user_id)
    if not user:
        raise HTTPException(
            status_code=404,
            detail={
                "success": False,
                "error_code": USER_NOT_FOUND,
                "message": "用户不存在",
            },
        )

    remaining = max(0, user["quota_daily"] - user["used_today"])

    return TokenResponse(
        success=True,
        message="Token 刷新成功",
        data={
            "token": new_token,
            "token_type": "bearer",
            "expires_in": 86400,
            "user": {
                "id": user["id"],
                "phone": user["phone"],
                "role": user["role"],
                "quota_daily": user["quota_daily"],
                "used_today": user["used_today"],
                "remaining": remaining,
            },
        },
    )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="获取当前用户信息",
    description=(
        "获取当前登录用户的详细信息，包括角色、额度使用情况等。\n"
        "需要 Bearer Token 鉴权。"
    ),
)
async def get_me(user: dict = Depends(get_current_user)) -> UserResponse:
    """
    获取当前用户信息。

    需要 Authorization: Bearer <token> 请求头。
    """
    user_id = user["user_id"]
    user_info = get_user(user_id)

    if not user_info:
        raise HTTPException(
            status_code=404,
            detail={
                "success": False,
                "error_code": USER_NOT_FOUND,
                "message": "用户不存在",
            },
        )

    quota = check_quota(user_id)
    remaining = max(0, quota.get("remaining", 0))

    return UserResponse(
        success=True,
        message="获取成功",
        data={
            "id": user_info["id"],
            "phone": user_info["phone"],
            "role": user_info["role"],
            "quota_daily": user_info["quota_daily"],
            "used_today": user_info["used_today"],
            "remaining": remaining,
            "last_reset": user_info["last_reset"],
            "created_at": user_info["created_at"],
        },
    )


@router.get(
    "/quota",
    response_model=UserResponse,
    summary="查询剩余额度",
    description="快速获取用户剩余生成次数（轻量端点）。",
)
async def get_quota(user: dict = Depends(get_current_user)) -> UserResponse:
    """查询用户剩余额度。"""
    user_id = user["user_id"]
    quota = check_quota(user_id)

    return UserResponse(
        success=True,
        message="查询成功",
        data=quota,
    )