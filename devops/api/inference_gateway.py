#!/usr/bin/env python3
"""
NexusVideo Inference Gateway — 云端 API 网关（FastAPI）

架构位置：
    用户浏览器/客户端 → SLB 443 → Nginx/Ingress → Inference Gateway (8000)
                                            → JWT 鉴权 + 额度校验
                                            → Redis 优先级任务队列
                                            → WebSocket 进度转发
                                            → 限流（IP + 用户级别）

职责：
    1. 接收前端生成请求（POST /api/v1/generate）
    2. JWT Token 鉴权 + 用户额度校验
    3. 将任务提交到 Redis 优先级队列（paid > free）
    4. WebSocket 转发 ComfyUI 生成进度到前端
    5. 限流保护后端 Worker 不被并发击垮
    6. 健康检查端点 /health + /ready

启动：
    uvicorn api_gateway:app --host 0.0.0.0 --port 8000 --workers 4
"""

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import redis.asyncio as aioredis
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("inference-gateway")

# ============================================================
# 配置（通过环境变量注入，K8s 中由 ConfigMap / Secret 提供）
# ============================================================
class Config:
    REDIS_URL: str = os.getenv(
        "REDIS_URL", "redis://:nexusvideo_redis@redis:6379/0"
    )
    DB_URL: str = os.getenv("DB_URL", "sqlite:///./nexusvideo.db")
    JWT_SECRET: str = os.getenv("JWT_SECRET", "nexusvideo-dev-jwt-secret")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    TOKEN_EXPIRY_HOURS: int = int(os.getenv("TOKEN_EXPIRY_HOURS", "168"))

    # 用户额度（每日生成次数）
    FREE_DAILY_LIMIT: int = int(os.getenv("FREE_DAILY_LIMIT", "5"))
    PAID_DAILY_LIMIT: int = int(os.getenv("PAID_DAILY_LIMIT", "100"))

    # 限流（每分钟请求数）
    IP_RATE_LIMIT_RPM: int = int(os.getenv("IP_RATE_LIMIT_RPM", "60"))
    USER_RATE_LIMIT_RPM: int = int(os.getenv("USER_RATE_LIMIT_RPM", "30"))

    # 队列配置
    QUEUE_KEY_FREE: str = os.getenv("QUEUE_KEY_FREE", "nexusvideo:queue:free")
    QUEUE_KEY_PAID: str = os.getenv("QUEUE_KEY_PAID", "nexusvideo:queue:paid")

    # WebSocket 配置
    WS_MAX_CONNECTIONS: int = int(os.getenv("WS_MAX_CONNECTIONS", "500"))

    # ComfyUI Worker 集群地址
    COMFYUI_WORKER_URLS: list[str] = (
        os.getenv("COMFYUI_WORKER_URLS", "").split(",")
        if os.getenv("COMFYUI_WORKER_URLS")
        else ["http://comfyui-worker-service:9000"]
    )


# ============================================================
# Pydantic 模型定义
# ============================================================

class Tier(str, Enum):
    FREE = "free"
    PAID = "paid"


class GenerateRequest(BaseModel):
    """前端提交的生成请求"""
    mode: str = Field(
        ..., description="生成模式",
        pattern="^(txt2video|img2video|video2video|style_transfer)$"
    )
    prompt: str = Field(..., description="文本提示词", min_length=1, max_length=2000)
    image_path: Optional[str] = Field(None, description="图生视频的首帧图 OSS URL")
    params: dict = Field(
        default_factory=dict,
        description="额外参数（duration, resolution, seed 等）"
    )


class GenerateResponse(BaseModel):
    """生成请求响应"""
    task_id: str
    queue_position: int
    estimated_seconds: int
    tier: Tier
    message: str


class TaskStatus(BaseModel):
    task_id: str
    state: str
    progress: int
    step: str
    output_path: Optional[str] = None
    error: Optional[str] = None
    queue_position: Optional[int] = None


class TokenPayload(BaseModel):
    user_id: str
    user_name: str
    tier: Tier
    exp: int


# ============================================================
# JWT 鉴权辅助函数
# ============================================================
# 生产环境建议使用 python-jose 或 PyJWT 库
# 此处为自包含实现，避免额外依赖

import hashlib
import hmac
import base64

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def create_token(payload: dict) -> str:
    """创建 JWT Token（HS256）"""
    header = {"alg": Config.JWT_ALGORITHM, "typ": "JWT"}
    exp = int(time.time()) + Config.TOKEN_EXPIRY_HOURS * 3600
    payload = {**payload, "exp": exp}

    h = _b64url_encode(json.dumps(header).encode())
    p = _b64url_encode(json.dumps(payload).encode())
    signing_input = f"{h}.{p}".encode()
    sig = hmac.new(
        Config.JWT_SECRET.encode(), signing_input, hashlib.sha256
    ).digest()
    return f"{h}.{p}.{_b64url_encode(sig)}"


async def verify_token(token: str) -> dict:
    """验证 JWT Token，返回 payload"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise HTTPException(status_code=401, detail="Invalid token format")
        header, payload_b64, sig = parts

        signing_input = f"{header}.{payload_b64}".encode()
        expected_sig = hmac.new(
            Config.JWT_SECRET.encode(), signing_input, hashlib.sha256
        ).digest()
        actual_sig = _b64url_decode(sig)
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise HTTPException(status_code=401, detail="Invalid token signature")

        payload = json.loads(_b64url_decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            raise HTTPException(status_code=401, detail="Token expired")
        return payload
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Token verification failed: {e}")


# ============================================================
# Redis 连接 + 限流 + 任务队列管理
# ============================================================

class RedisManager:
    """Redis 管理器：任务队列 + 限流 + 会话缓存"""

    def __init__(self, url: str):
        self.url = url
        self.redis: Optional[aioredis.Redis] = None

    async def connect(self):
        self.redis = aioredis.from_url(self.url, decode_responses=True)
        await self.redis.ping()
        logger.info("Redis 连接成功: %s", self.url)

    async def disconnect(self):
        if self.redis:
            await self.redis.close()

    # --- 任务队列 ---

    async def enqueue_task(self, task_data: dict, tier: Tier) -> int:
        """
        将任务提交到对应优先级的 Redis Sorted Set
        付费用户（paid）→ 高优先级队列（score 较小 = 优先出队）
        免费用户（free）→ 低优先级队列
        score = 优先级值 + 提交时间（同优先级按时间排序）
        """
        now = time.time()
        priority = 0 if tier == Tier.PAID else 1000
        score = priority + now
        task_id = task_data["task_id"]

        queue_key = (
            Config.QUEUE_KEY_PAID if tier == Tier.PAID else Config.QUEUE_KEY_FREE
        )
        await self.redis.zadd(queue_key, {task_id: score})
        # 保存任务数据到 hash
        await self.redis.hset(
            "nexusvideo:tasks", task_id, json.dumps(task_data)
        )
        # 返回队列位置（当前任务在该队列中的排名）
        position = await self.redis.zrank(queue_key, task_id) or 0
        logger.info(
            "任务 %s 已入队 [%s], 位置=%d, tier=%s",
            task_id, queue_key, position, tier.value
        )
        return position

    async def get_queue_position(self, task_id: str, tier: Tier) -> int:
        """查询任务在队列中的位置"""
        queue_key = (
            Config.QUEUE_KEY_PAID if tier == Tier.PAID else Config.QUEUE_KEY_FREE
        )
        return await self.redis.zrank(queue_key, task_id) or 0

    async def get_queue_depth(self) -> int:
        """获取总队列深度"""
        free_len = await self.redis.zcard(Config.QUEUE_KEY_FREE) or 0
        paid_len = await self.redis.zcard(Config.QUEUE_KEY_PAID) or 0
        return free_len + paid_len

    # --- 限流 ---

    async def check_rate_limit(self, key: str, limit: int, window: int = 60) -> bool:
        """
        滑动窗口限流
        返回 True = 允许, False = 超出限制
        """
        pipe = self.redis.pipeline()
        now = time.time()
        window_start = now - window

        # 删除过期记录
        pipe.zremrangebyscore(key, "-inf", window_start)
        # 计数
        pipe.zcard(key)
        results = await pipe.execute()

        if results[1] >= limit:
            return False

        # 记录本次请求
        await self.redis.zadd(key, {f"{now}:{uuid.uuid4().hex[:8]}": now})
        return True

    # --- 额度校验 ---

    async def check_quota(self, user_id: str, tier: Tier) -> bool:
        """检查用户今日剩余额度"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        quota_key = f"nexusvideo:quota:{user_id}:{today}"
        used = await self.redis.get(quota_key) or 0
        used = int(used)
        limit = Config.PAID_DAILY_LIMIT if tier == Tier.PAID else Config.FREE_DAILY_LIMIT
        if used >= limit:
            return False
        return True

    async def consume_quota(self, user_id: str):
        """消耗一次用户额度"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        quota_key = f"nexusvideo:quota:{user_id}:{today}"
        await self.redis.incr(quota_key)
        # 设置过期时间（凌晨 1 点自动清零）
        tomorrow = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
        ttl = int((tomorrow - datetime.now(timezone.utc)).total_seconds())
        await self.redis.expire(quota_key, ttl)


# ============================================================
# WebSocket 进度转发管理器
# ============================================================

class WebSocketManager:
    """
    管理 WebSocket 连接，将 ComfyUI 进度转发到前端
    每个前端连接通过 task_id 订阅对应任务的进度更新
    """

    def __init__(self):
        # task_id → [WebSocket, ...]  一个任务可能被多个客户端订阅
        self.subscriptions: dict[str, list[WebSocket]] = {}
        # WebSocket → task_id
        self.connections: dict[WebSocket, str] = {}

    async def subscribe(self, ws: WebSocket, task_id: str):
        if task_id not in self.subscriptions:
            self.subscriptions[task_id] = []
        self.subscriptions[task_id].append(ws)
        self.connections[ws] = task_id
        await ws.send_json({
            "type": "subscribed",
            "task_id": task_id,
            "message": f"已订阅任务 {task_id} 的进度更新"
        })

    async def unsubscribe(self, ws: WebSocket):
        task_id = self.connections.pop(ws, None)
        if task_id and task_id in self.subscriptions:
            if ws in self.subscriptions[task_id]:
                self.subscriptions[task_id].remove(ws)
            if not self.subscriptions[task_id]:
                del self.subscriptions[task_id]

    async def broadcast_progress(self, task_id: str, data: dict):
        """向订阅了该任务的所有 WebSocket 发送进度"""
        if task_id not in self.subscriptions:
            return
        for ws in list(self.subscriptions[task_id]):
            try:
                await ws.send_json({
                    "type": "progress",
                    "task_id": task_id,
                    **data
                })
            except Exception:
                # WebSocket 断开时清理
                await self.unsubscribe(ws)


# ============================================================
# 全局实例
# ============================================================

redis_manager = RedisManager(Config.REDIS_URL)
ws_manager = WebSocketManager()


# ============================================================
# 应用生命周期
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    await redis_manager.connect()
    logger.info("Inference Gateway 已启动")
    yield
    # 关闭时
    await redis_manager.disconnect()
    logger.info("Inference Gateway 已关闭")


app = FastAPI(
    title="NexusVideo Inference Gateway",
    version="1.0.0",
    description="NexusVideo AI 视频生成 API 网关 — 鉴权、队列、限流、WebSocket",
    lifespan=lifespan,
)


# ============================================================
# 依赖注入
# ============================================================

async def get_current_user(request: Request) -> TokenPayload:
    """
    JWT 鉴权：从 Authorization header 提取 Bearer Token
    返回用户信息（user_id, tier 等）
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth_header.removeprefix("Bearer ")
    payload = await verify_token(token)
    return TokenPayload(**payload)


async def get_client_ip(request: Request) -> str:
    """获取真实客户端 IP（穿透 Nginx 代理）"""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host or "127.0.0.1"


# ============================================================
# 端点定义
# ============================================================

@app.get("/health")
async def health():
    """Liveness probe — 仅检查进程存活，用于 K8s 存活探针"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/ready")
async def ready():
    """
    Readiness probe — 检查是否可以接新请求
    检查 Redis 连接 + 队列深度
    """
    try:
        await redis_manager.redis.ping()
        queue_depth = await redis_manager.get_queue_depth()
        workers_ready = len(Config.COMFYUI_WORKER_URLS)
        return {
            "status": "ready",
            "redis": "connected",
            "queue_depth": queue_depth,
            "workers_ready": workers_ready,
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "error": str(e)}
        )


@app.post("/api/v1/generate", response_model=GenerateResponse)
async def generate(
    request: GenerateRequest,
    user: TokenPayload = Depends(get_current_user),
    client_ip: str = Depends(get_client_ip),
):
    """
    核心端点：接收前端生成请求
    流程：
        1. JWT 鉴权
        2. 用户级别限流（每分钟最多 N 次）
        3. IP 级别限流（防止 DDoS）
        4. 每日额度校验
        5. 将任务提交到 Redis 优先级队列
        6. 返回 task_id + 队列位置
    """
    # ---- 限流：用户级别 ----
    user_key = f"nexusvideo:ratelimit:user:{user.user_id}"
    if not await redis_manager.check_rate_limit(
        user_key, Config.USER_RATE_LIMIT_RPM
    ):
        raise HTTPException(
            status_code=429,
            detail=f"User rate limit exceeded ({Config.USER_RATE_LIMIT_RPM}/min)"
        )

    # ---- 限流：IP 级别 ----
    ip_key = f"nexusvideo:ratelimit:ip:{client_ip}"
    if not await redis_manager.check_rate_limit(
        ip_key, Config.IP_RATE_LIMIT_RPM
    ):
        raise HTTPException(
            status_code=429,
            detail=f"IP rate limit exceeded ({Config.IP_RATE_LIMIT_RPM}/min)"
        )

    # ---- 额度校验 ----
    if not await redis_manager.check_quota(user.user_id, user.tier):
        daily_limit = (
            Config.PAID_DAILY_LIMIT if user.tier == Tier.PAID else Config.FREE_DAILY_LIMIT
        )
        raise HTTPException(
            status_code=403,
            detail=f"每日额度已用尽（{daily_limit} 次/天），请升级套餐或明天再试"
        )

    # ---- 消耗额度 ----
    await redis_manager.consume_quota(user.user_id)

    # ---- 构建任务数据 ----
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    task_data = {
        "task_id": task_id,
        "user_id": user.user_id,
        "tier": user.tier.value,
        "mode": request.mode,
        "prompt": request.prompt,
        "image_path": request.image_path,
        "params": request.params,
        "state": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # ---- 入队 ----
    queue_position = await redis_manager.enqueue_task(task_data, user.tier)

    # ---- 估算完成时间（基于队列深度和平均处理时间）----
    avg_process_seconds = 90  # 单次生成平均 90 秒
    total_queue = queue_position + 1
    estimated = total_queue * avg_process_seconds

    return GenerateResponse(
        task_id=task_id,
        queue_position=queue_position,
        estimated_seconds=estimated,
        tier=user.tier,
        message=(
            "任务已提交，正在排队中"
            if queue_position > 0
            else "任务已提交，正在处理中"
        ),
    )


@app.get("/api/v1/tasks/{task_id}", response_model=TaskStatus)
async def get_task_status(task_id: str, user: TokenPayload = Depends(get_current_user)):
    """查询任务状态"""
    task_json = await redis_manager.redis.hget("nexusvideo:tasks", task_id)
    if not task_json:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = json.loads(task_json)
    tier = Tier(task.get("tier", "free"))
    queue_position = await redis_manager.get_queue_position(task_id, tier)

    return TaskStatus(
        task_id=task_id,
        state=task.get("state", "unknown"),
        progress=task.get("progress", 0),
        step=task.get("step", ""),
        output_path=task.get("output_path"),
        error=task.get("error"),
        queue_position=queue_position,
    )


@app.websocket("/ws/progress")
async def websocket_progress(
    ws: WebSocket,
    task_id: str = Query(..., description="要订阅的任务 ID"),
):
    """
    WebSocket 端点：订阅任务进度更新
    前端通过 task_id 连接此 WebSocket，接收 ComfyUI 实时进度推送

    用法示例：
        ws = new WebSocket(
          `wss://api.nexusvideo.com/ws/progress?task_id=${taskId}`
        );
        ws.onmessage = (e) => console.log(JSON.parse(e.data));
    """
    try:
        # 接入握手
        await ws.accept()
        await ws_manager.subscribe(ws, task_id)

        # 保持连接（心跳）
        while True:
            data = await ws.receive_text()
            # 前端可发送 {"type": "ping"} 来保持连接
            if data.strip() == '{"type": "ping"}':
                await ws.send_json({"type": "pong"})
            else:
                # 忽略未知消息
                pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("WebSocket 异常: %s", e)
    finally:
        await ws_manager.unsubscribe(ws)


@app.post("/api/v1/tasks/{task_id}/progress")
async def update_task_progress(
    task_id: str,
    progress: int = Query(..., ge=0, le=100),
    step: str = Query(""),
    worker: str = Query("unknown"),
):
    """
    内部端点（仅 ComfyUI Worker 调用）：上报任务进度
    Worker 在处理过程中调用此端点，网关通过 WebSocket 转发到前端

    生产环境应对该端点做 IP 白名单限制（仅允许 VPC 内网访问）
    """
    task_json = await redis_manager.redis.hget("nexusvideo:tasks", task_id)
    if not task_json:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = json.loads(task_json)
    task["progress"] = progress
    task["step"] = step
    task["state"] = "running"
    await redis_manager.redis.hset("nexusvideo:tasks", task_id, json.dumps(task))

    # 通过 WebSocket 转发到前端
    await ws_manager.broadcast_progress(task_id, {
        "progress": progress,
        "step": step,
        "worker": worker,
    })

    return {"status": "ok"}


@app.post("/api/v1/tasks/{task_id}/complete")
async def complete_task(
    task_id: str,
    output_path: str = Query(...),
    worker: str = Query("unknown"),
):
    """Worker 完成处理后的回调，标记任务完成并触发 WebSocket 通知"""
    task_json = await redis_manager.redis.hget("nexusvideo:tasks", task_id)
    if not task_json:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = json.loads(task_json)
    task["state"] = "completed"
    task["progress"] = 100
    task["output_path"] = output_path
    await redis_manager.redis.hset("nexusvideo:tasks", task_id, json.dumps(task))

    # 从队列中移除
    tier = Tier(task.get("tier", "free"))
    queue_key = (
        Config.QUEUE_KEY_PAID if tier == Tier.PAID else Config.QUEUE_KEY_FREE
    )
    await redis_manager.redis.zrem(queue_key, task_id)

    # WebSocket 通知前端
    await ws_manager.broadcast_progress(task_id, {
        "type": "completed",
        "output_path": output_path,
    })

    return {"status": "ok"}


@app.post("/api/v1/tasks/{task_id}/error")
async def error_task(
    task_id: str,
    error: str = Query(""),
    worker: str = Query("unknown"),
):
    """Worker 处理失败时的回调"""
    task_json = await redis_manager.redis.hget("nexusvideo:tasks", task_id)
    if not task_json:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = json.loads(task_json)
    task["state"] = "failed"
    task["error"] = error
    await redis_manager.redis.hset("nexusvideo:tasks", task_id, json.dumps(task))

    # WebSocket 通知
    await ws_manager.broadcast_progress(task_id, {
        "type": "error",
        "error": error,
    })

    return {"status": "ok"}


# ============================================================
# 启动入口
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "inference_gateway:app",
        host="0.0.0.0",
        port=8000,
        workers=4,
        log_level="info",
    )