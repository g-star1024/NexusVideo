"""
NexusVideo Backend - 用户认证核心模块
============================================================
架构位置：core/auth.py
被 routers/auth.py 和 routers/cloud_forward.py 使用。

职责：
  1. JWT Token 生成与验证（HS256）
  2. 密码哈希与校验（bcrypt）
  3. 角色定义（free / paid）
  4. FastAPI 依赖注入（get_current_user）

设计要点：
  - JWT 密钥从环境变量 NEXUS_JWT_SECRET 读取
  - 默认 24 小时有效期，可通过 JWT_EXPIRE_HOURS 覆盖
  - 密码使用 bcrypt 加盐哈希（自动加盐 + 迭代次数可调）
  - 失败时抛出 NexusError（统一错误码 14xxx = 用户认证模块）
"""

import hmac
import hashlib
import base64
import json
import time
import re
from typing import Optional
from fastapi import Request, HTTPException
from loguru import logger

from config import settings
from exceptions import NexusError, ErrorCode


# ================================================================
# 错误码扩展（14xxx = 用户认证模块）
# ================================================================
AUTH_ERROR = "14001"        # 通用认证错误
TOKEN_INVALID = "14002"     # Token 无效
TOKEN_EXPIRED = "14003"     # Token 已过期
PHONE_EXISTS = "14004"      # 手机号已注册
USER_NOT_FOUND = "14005"    # 用户不存在
PASSWORD_WRONG = "14006"    # 密码错误
QUOTA_EXHAUSTED = "14007"   # 额度已用尽


# ================================================================
# 角色枚举（与前端 / 云端 API 网关对齐）
# ================================================================
ROLE_FREE = "free"
ROLE_PAID = "paid"

# 各角色的每日生成额度
QUOTA_PER_ROLE = {
    ROLE_FREE: 5,
    ROLE_PAID: 100,
}


# ================================================================
# 手机号校验正则
# ================================================================
# 中国大陆手机号：以 1 开头，第二位 3-9，共 11 位
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


def validate_phone(phone: str) -> str:
    """
    校验手机号格式，返回标准化的手机号字符串。

    支持输入：13812345678 / +86 138 1234 5678 / 138-1234-5678
    返回标准化后的纯数字字符串。
    """
    cleaned = phone.replace("+86", "").replace("+", "").replace("-", "").replace(" ", "")
    if not PHONE_PATTERN.match(cleaned):
        raise NexusError(
            message="手机号格式不正确，请输入有效的 11 位中国大陆手机号",
            error_code=AUTH_ERROR,
            status_code=400,
        )
    return cleaned


# ================================================================
# JWT Token 生成与验证（自包含实现，不依赖外部库）
# ================================================================

def _get_jwt_secret() -> str:
    """
    获取 JWT 密钥。

    优先从 settings 读取（支持 NEXUS_JWT_SECRET 环境变量覆盖）。
    若未配置，使用默认开发密钥（生产环境必须覆盖！）。
    """
    # settings 中通过 env_prefix="NEXUS_" 自动映射 NEXUS_JWT_SECRET
    secret = getattr(settings, "jwt_secret", None)
    if not secret:
        import os
        secret = os.getenv("NEXUS_JWT_SECRET", "dev-secret-change-in-production")
        logger.warning("JWT 密钥未配置，使用默认开发密钥！生产环境请务必设置 NEXUS_JWT_SECRET")
    return secret


def _get_jwt_expire_hours() -> int:
    """获取 Token 有效期（小时）。"""
    import os
    return int(os.getenv("JWT_EXPIRE_HOURS", "24"))


def _b64url_encode(data: bytes) -> str:
    """Base64 URL 安全编码（无填充）。"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    """Base64 URL 安全解码（补回填充）。"""
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def create_access_token(user_id: str, expires_delta: Optional[float] = None) -> str:
    """
    生成访问 Token。

    参数：
        user_id: 用户唯一标识
        expires_delta: Token 有效期（秒），默认使用全局配置

    返回：
        JWT 字符串（header.payload.signature）

    Token 结构（HS256）：
        Header:
            { "alg": "HS256", "typ": "JWT" }
        Payload:
            { "user_id": "...", "exp": 1700000000, "iat": 1699999000 }
    """
    secret = _get_jwt_secret()
    now = int(time.time())
    expires_in = int(expires_delta) if expires_delta else _get_jwt_expire_hours() * 3600

    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "user_id": user_id,
        "iat": now,
        "exp": now + expires_in,
    }

    h = _b64url_encode(json.dumps(header, ensure_ascii=False).encode())
    p = _b64url_encode(json.dumps(payload, ensure_ascii=False).encode())
    signing_input = f"{h}.{p}".encode()
    sig = hmac.new(
        secret.encode(), signing_input, hashlib.sha256
    ).digest()

    token = f"{h}.{p}.{_b64url_encode(sig)}"
    logger.info(f"已生成 JWT Token，user_id={user_id}, 有效期={expires_in}秒")
    return token


def verify_token(token: str) -> dict:
    """
    验证 JWT Token，返回 payload。

    验证流程：
        1. 格式校验（三段式）
        2. 签名校验（HMAC-SHA256）
        3. 过期时间校验
        4. 必须包含 user_id 字段

    失败时抛出 NexusError（统一错误码）。
    """
    secret = _get_jwt_secret()

    # Step 1: 格式校验
    parts = token.split(".")
    if len(parts) != 3:
        raise NexusError(
            message="Token 格式无效",
            error_code=TOKEN_INVALID,
            status_code=401,
        )

    header_b64, payload_b64, sig_b64 = parts

    # Step 2: 签名校验
    try:
        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected_sig = hmac.new(
            secret.encode(), signing_input, hashlib.sha256
        ).digest()
        actual_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise NexusError(
                message="Token 签名验证失败",
                error_code=TOKEN_INVALID,
                status_code=401,
            )
    except NexusError:
        raise
    except Exception as e:
        logger.error(f"Token 签名校验异常：{e}")
        raise NexusError(
            message="Token 验证失败",
            error_code=TOKEN_INVALID,
            status_code=401,
        )

    # Step 3: 解码 payload
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as e:
        logger.error(f"Token payload 解码失败：{e}")
        raise NexusError(
            message="Token 载荷无效",
            error_code=TOKEN_INVALID,
            status_code=401,
        )

    # Step 4: 过期时间校验
    exp = payload.get("exp", 0)
    if exp <= int(time.time()):
        raise NexusError(
            message="Token 已过期，请重新登录",
            error_code=TOKEN_EXPIRED,
            status_code=401,
        )

    # Step 5: 必须包含 user_id
    user_id = payload.get("user_id")
    if not user_id:
        raise NexusError(
            message="Token 缺少用户信息",
            error_code=TOKEN_INVALID,
            status_code=401,
        )

    logger.debug(f"Token 验证成功，user_id={user_id}")
    return payload


# ================================================================
# 密码哈希（bcrypt）
# ================================================================
def hash_password(password: str) -> str:
    """
    使用 bcrypt 对密码进行加盐哈希。

    bcrypt 自动包含盐值（salt）在哈希字符串中，因此无需单独存储盐。
    返回格式：$2b$<cost>$<22位盐值><31位哈希值>（共 60 字符）

    注意：bcrypt 是 CPU 密集型操作，不适合在高并发场景大量使用。
    MVP 阶段直接使用；生产环境建议使用独立 Worker 处理注册流程。
    """
    import bcrypt

    pwd_bytes = password.encode("utf-8")
    hashed = bcrypt.hashpw(pwd_bytes, bcrypt.gensalt(rounds=12))
    return hashed.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """
    验证密码与哈希是否匹配。

    bcrypt 使用常时间比较（constant-time comparison），
    防止时序攻击（timing attack）泄露密码信息。
    """
    import bcrypt

    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            hashed.encode("utf-8"),
        )
    except Exception as e:
        logger.error(f"密码校验异常：{e}")
        return False


# ================================================================
# FastAPI 依赖注入
# ================================================================
async def get_current_user(request: Request) -> dict:
    """
    FastAPI 依赖注入函数：从 Authorization Header 提取 Bearer Token，
    验证后返回用户信息。

    用法：
        @router.get("/protected")
        async def protected(user: dict = Depends(get_current_user)):
            return {"user_id": user["user_id"]}

    返回结构：
        {
            "user_id": "string",
            "exp": int,
            "iat": int,
        }

    失败时抛出 HTTPException（401），由 FastAPI 自动处理。
    """
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": TOKEN_INVALID,
                "message": "缺少 Bearer Token，请在请求头中设置 Authorization: Bearer <token>",
            },
        )

    token = auth_header.removeprefix("Bearer ")
    if not token.strip():
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": TOKEN_INVALID,
                "message": "Token 为空",
            },
        )

    try:
        payload = verify_token(token)
    except NexusError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={
                "error_code": e.error_code,
                "message": e.message,
            },
        )

    return payload