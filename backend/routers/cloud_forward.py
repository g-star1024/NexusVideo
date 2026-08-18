"""
NexusVideo Backend - 云端 API 转发层
============================================================
架构位置：routers/cloud_forward.py
被 local_server.py 注册为 /api/v1/cloud/* 路由。

职责：
  1. 转发本地前端请求到云端 API 网关（inference_gateway.py）
  2. 做 JWT 鉴权 + 用户额度校验（与本地认证系统联动）
  3. 代理云端 WebSocket 进度推送到本地前端
  4. 云端不可用时自动降级到本地模式（含错误提示文案）

与 devops/api/inference_gateway.py 的协作关系：
  本地 FastAPI 客户端 ←─── 本模块 ───→ 云端 API 网关
                    HTTP POST            HTTP POST
                  /cloud/generate      /api/v1/generate
                    GET /ws            WS /ws/progress
                  /cloud/task/{id}     /api/v1/tasks/{id}

错误降级策略（白皮书 4.3 节）：
  云端返回 503/超时 → 提示"云端服务暂时繁忙，已自动切换至本地生成"
  云端返回 401 → 提示"云端认证失败，请重新登录"
  云端返回 403 → 提示"今日云端额度已用尽"
  云端返回 429 → 提示"云端请求过于频繁，请稍后重试或切换本地"

环境变量：
  NEXUS_CLOUD_API_URL — 云端 API 网关地址（默认 http://localhost:9000）
  CLOUD_API_BASE_URL  — 备用环境变量（兼容旧配置）
"""

import json
import time
import uuid
from typing import Optional
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field
from loguru import logger

import httpx
import websockets

from config import settings
from core.auth import get_current_user, verify_token
from core.user_db import ensure_db_ready, get_user, update_quota, check_quota
from core.task_manager import task_manager
from exceptions import NexusError, ErrorCode

router = APIRouter(prefix="/api/v1/cloud", tags=["云端"])


# ================================================================
# 配置
# ================================================================
def _get_cloud_api_url() -> str:
    """
    获取云端 API 网关地址。

    优先级：settings.cloud_endpoint > NEXUS_CLOUD_API_URL > CLOUD_API_BASE_URL > 默认
    开发环境默认指向本地（localhost:9000），模拟云端 API 网关。
    """
    if settings.cloud_endpoint:
        return settings.cloud_endpoint

    import os
    cloud_url = (
        os.getenv("NEXUS_CLOUD_API_URL")
        or os.getenv("CLOUD_API_BASE_URL")
        or "http://localhost:9000"
    )
    return cloud_url


CLOUD_API_URL = _get_cloud_api_url()
CLOUD_TIMEOUT = 30  # 单次请求超时（秒）
WS_CONNECT_TIMEOUT = 10  # WebSocket 连接超时（秒）


# ================================================================
# 错误降级映射表
# ================================================================
# 云端错误 → 本地降级提示文案（前端展示给用户）
CLOUD_ERROR_FALLOVER = {
    503: {
        "message": "云端服务暂时繁忙，已自动切换至本地生成，请稍候...",
        "error_code": "10002",
        "auto_fallback": True,
    },
    504: {
        "message": "云端服务响应超时，已自动切换至本地生成",
        "error_code": "12002",
        "auto_fallback": True,
    },
    401: {
        "message": "云端认证失败，请重新登录后再试",
        "error_code": "14002",
        "auto_fallback": False,
    },
    403: {
        "message": "今日云端生成额度已用尽，请稍后再试或使用本地模式",
        "error_code": "14007",
        "auto_fallback": False,
    },
    429: {
        "message": "云端请求过于频繁，请稍后重试或切换至本地模式",
        "error_code": "14002",
        "auto_fallback": False,
    },
}


# ================================================================
# 请求模型
# ================================================================
class CloudGenerateRequest(BaseModel):
    """云端生成请求体（与 devops/api/inference_gateway.py 对齐）。"""
    mode: str = Field(
        ...,
        description="生成模式",
        pattern=r"^(txt2video|img2video|video2video|style_transfer)$",
        examples=["txt2video"],
    )
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="文本提示词",
    )
    image_path: Optional[str] = Field(
        None,
        description="图生视频的首帧图路径（本地路径或 OSS URL）",
    )
    params: dict = Field(
        default_factory=dict,
        description="额外参数（width, height, frames, steps, cfg, seed 等）",
    )


class CloudTaskResponse(BaseModel):
    """云端任务提交响应。"""
    success: bool = Field(default=True)
    error_code: Optional[str] = Field(None)
    message: str = Field(..., description="提示信息")
    data: dict = Field(..., description="云端返回的任务信息")


class CloudTaskStatus(BaseModel):
    """云端任务状态查询响应。"""
    success: bool = Field(default=True)
    error_code: Optional[str] = Field(None)
    message: str = Field(default="查询成功")
    data: dict = Field(..., description="任务状态信息")


class CloudFallbackInfo(BaseModel):
    """云端降级信息（供前端展示）。"""
    success: bool = Field(default=True)
    message: str = Field(..., description="降级提示文案")
    data: dict = Field(
        default_factory=dict,
        description="降级详情（fallback_to, reason 等）",
    )


# ================================================================
# 便捷 HTTP 客户端
# ================================================================
async def _cloud_http_post(
    path: str,
    json_data: dict,
    token: Optional[str] = None,
) -> tuple[int, dict]:
    """
    向云端 API 网关发送 HTTP POST 请求。

    返回：(status_code, response_body)
    超时或网络错误返回 (0, {"error": "connection_error"})
    """
    url = f"{CLOUD_API_URL}{path}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=CLOUD_TIMEOUT) as client:
            resp = await client.post(url, json=json_data, headers=headers)
            try:
                body = resp.json()
            except json.JSONDecodeError:
                body = {"raw": resp.text[:500]}
            return resp.status_code, body
    except httpx.TimeoutException:
        logger.error(f"云端 API 超时：{url}")
        return 504, {"error": "timeout"}
    except httpx.ConnectError as e:
        logger.error(f"云端 API 连接失败：{e}")
        return 503, {"error": "connection_refused", "detail": str(e)}
    except Exception as e:
        logger.error(f"云端 API 请求异常：{e}")
        return 0, {"error": "unknown", "detail": str(e)}


async def _cloud_http_get(
    path: str,
    token: Optional[str] = None,
) -> tuple[int, dict]:
    """向云端 API 网关发送 HTTP GET 请求。"""
    url = f"{CLOUD_API_URL}{path}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=CLOUD_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
            try:
                body = resp.json()
            except json.JSONDecodeError:
                body = {"raw": resp.text[:500]}
            return resp.status_code, body
    except httpx.TimeoutException:
        return 504, {"error": "timeout"}
    except httpx.ConnectError as e:
        logger.error(f"云端 API 连接失败：{e}")
        return 503, {"error": "connection_refused"}
    except Exception as e:
        logger.error(f"云端 API 请求异常：{e}")
        return 0, {"error": "unknown", "detail": str(e)}


# ================================================================
# 端点实现
# ================================================================
@router.post(
    "/generate",
    response_model=CloudTaskResponse,
    summary="云端视频生成",
    description=(
        "将生成请求转发到云端 API 网关。\n\n"
        "流程：\n"
        "  1. 验证 JWT Token + 用户额度\n"
        "  2. 转发到云端 POST /api/v1/generate\n"
        "  3. 返回云端 task_id + 队列位置\n\n"
        "错误降级：\n"
        "  - 云端 503/超时 → 返回降级提示（auto_fallback=true）\n"
        "  - 前端收到 auto_fallback 后自动调用本地 /generate\n"
    ),
)
async def cloud_generate(
    request: CloudGenerateRequest,
    user: dict = Depends(get_current_user),
) -> CloudTaskResponse:
    """
    云端视频生成转发。

    完整流程：
        1. 从 JWT 提取 user_id
        2. 从用户数据库查询用户信息和剩余额度
        3. 若本地剩余额度不足，直接拒绝
        4. 构建请求体转发到云端 API 网关
        5. 云端返回成功后，本地消耗一次额度
        6. 返回云端 task_id
    """
    user_id = user["user_id"]
    ensure_db_ready()

    # Step 1: 检查本地用户额度
    quota = check_quota(user_id)
    if quota.get("remaining", 0) <= 0:
        raise HTTPException(
            status_code=403,
            detail={
                "success": False,
                "error_code": "14007",
                "message": f"今日生成额度已用尽（{quota.get('quota_daily', 5)} 次/天），"
                           f"请明天再试或升级付费套餐",
            },
        )

    # Step 2: 构建转发请求体
    forward_payload = {
        "mode": request.mode,
        "prompt": request.prompt,
        "image_path": request.image_path,
        "params": request.params,
    }

    logger.info(
        f"云端生成请求：user_id={user_id}, mode={request.mode}, "
        f"prompt='{request.prompt[:60]}...'"
    )

    # Step 3: 转发到云端 API 网关
    status_code, body = await _cloud_http_post(
        "/api/v1/generate",
        json_data=forward_payload,
        token=create_forward_token(user_id),
    )

    # Step 4: 处理云端响应
    if status_code == 200:
        # 云端成功：本地消耗额度
        update_quota(user_id)

        # 将云端 task_id 记录到本地任务管理器（方便前端查询）
        cloud_task_id = body.get("task_id", "")
        if cloud_task_id:
            # 在本地任务表注册一个占位记录
            from models.schemas import TaskStatus, GenerationMode
            mode = GenerationMode.TXT2VIDEO
            for m in GenerationMode:
                if m.value == request.mode:
                    mode = m
                    break

            record = type("CloudTaskRecord", (), {})()
            record.task_id = cloud_task_id
            record.prompt = request.prompt
            record.mode = mode
            record.seed = request.params.get("seed", 0)
            record.status = TaskStatus.QUEUED
            record.progress = 0.0
            record.stage_message = "云端任务已提交，正在排队..."
            record.output_url = None
            record.output_filename = None
            record.error = None
            record.error_code = None
            record.created_at = time.time()
            record.completed_at = None
            record.retry_count = 0
            record.degradation_level = 0
            task_manager._tasks[cloud_task_id] = record
            logger.info(f"云端任务已登记：{cloud_task_id}")

        return CloudTaskResponse(
            success=True,
            message=body.get("message", "云端任务已提交"),
            data=body,
        )

    # Step 5: 云端错误处理 + 降级
    fallback = CLOUD_ERROR_FALLOVER.get(status_code)
    if fallback:
        logger.warning(
            f"云端返回 {status_code}，执行降级：{fallback['message']}"
        )
        return CloudTaskResponse(
            success=True,
            message=fallback["message"],
            data={
                "cloud_error": True,
                "auto_fallback": fallback["auto_fallback"],
                "cloud_status_code": status_code,
                "fallback_to": "local" if fallback["auto_fallback"] else None,
                "cloud_body": body,
            },
        )

    # 未知错误
    logger.error(f"云端返回未知状态码：{status_code}, body={body}")
    raise HTTPException(
        status_code=502,
        detail={
            "success": False,
            "error_code": "10002",
            "message": f"云端服务返回异常（HTTP {status_code}），请稍后重试",
            "detail": {"cloud_status_code": status_code, "cloud_body": body},
        },
    )


@router.get(
    "/task/{task_id}",
    response_model=CloudTaskStatus,
    summary="查询云端任务状态",
    description=(
        "转发到云端查询任务状态。\n\n"
        "与本地 GET /task/{task_id} 互补：\n"
        "  - 本地任务：GET /task/{task_id}\n"
        "  - 云端任务：GET /api/v1/cloud/task/{task_id}"
    ),
)
async def cloud_task_status(
    task_id: str,
    user: dict = Depends(get_current_user),
) -> CloudTaskStatus:
    """
    查询云端任务状态。

    流程：
        1. 先尝试本地任务表（降级后或云端同步中）
        2. 本地无记录时转发到云端
    """
    # 先在本地任务表查找（降级后或已同步的任务）
    local_record = task_manager.get_task_status(task_id)
    if local_record is not None and local_record.status.value in ("success", "failed", "timeout"):
        return CloudTaskStatus(
            success=True,
            message="本地查询成功",
            data={
                "task_id": task_id,
                "state": local_record.status.value,
                "progress": local_record.progress,
                "step": local_record.stage_message,
                "output_path": local_record.output_url,
                "error": local_record.error,
            },
        )

    # 转发到云端查询
    status_code, body = await _cloud_http_get(
        f"/api/v1/tasks/{task_id}",
        token=create_forward_token(user["user_id"]),
    )

    if status_code == 200:
        return CloudTaskStatus(
            success=True,
            message="查询成功",
            data=body,
        )

    if status_code == 404:
        raise HTTPException(
            status_code=404,
            detail={
                "success": False,
                "error_code": "12001",
                "message": f"任务不存在：{task_id}",
            },
        )

    # 云端不可用，尝试返回本地记录
    if local_record is not None:
        return CloudTaskStatus(
            success=True,
            message="云端查询失败，返回本地缓存状态",
            data={
                "task_id": task_id,
                "state": local_record.status.value,
                "progress": local_record.progress,
                "step": local_record.stage_message,
                "error": local_record.error,
                "source": "local_cache",
            },
        )

    raise HTTPException(
        status_code=502,
        detail={
            "success": False,
            "error_code": "10002",
            "message": "云端服务不可用，无法查询任务状态",
        },
    )


@router.websocket("/progress/ws")
async def cloud_progress_websocket(
    websocket: WebSocket,
    task_id: str = Query(..., description="云端任务 ID"),
):
    """
    WebSocket 端点：代理云端 API 网关的进度推送。

    架构：
        前端 ← WS → 本地 /api/v1/cloud/progress/ws?task_id=xxx
                     ↓
                  本地 WS 连接 → 云端 /ws/progress?task_id=xxx
                     ↓
                  ComfyUI Worker → 云端 WebSocketManager → 转发

    降级策略：
        如果无法连接到云端 WebSocket，返回错误消息后关闭连接。
        前端可退回到 GET /api/v1/cloud/task/{task_id} 轮询。
    """
    try:
        # 验证 task_id 格式
        if not task_id or len(task_id) < 8:
            await websocket.accept()
            await websocket.send_json({
                "type": "error",
                "message": "task_id 无效",
                "task_id": task_id,
            })
            await websocket.close()
            return

        await websocket.accept()
        logger.info(f"云端进度 WebSocket 代理连接：task_id={task_id}")

        # 发送连接确认
        await websocket.send_json({
            "type": "connected",
            "task_id": task_id,
            "message": f"已连接到云端任务 {task_id} 的进度通道",
        })

        # 确定 WebSocket URL（将 http:// → ws://, https:// → wss://）
        ws_url = CLOUD_API_URL.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/ws/progress?task_id={task_id}"

        # 连接云端 WebSocket
        logger.info(f"正在连接云端 WebSocket：{ws_url}")

        try:
            async with websockets.connect(
                ws_url,
                timeout=WS_CONNECT_TIMEOUT,
            ) as cloud_ws:
                logger.info("云端 WebSocket 连接成功，开始双向代理...")

                # 启动云端 → 本地 的消息转发任务
                async def forward_cloud_to_local():
                    try:
                        async for message in cloud_ws:
                            try:
                                data = json.loads(message)
                                # 添加来源标识
                                data["source"] = "cloud"
                                await websocket.send_json(data)
                            except json.JSONDecodeError:
                                await websocket.send_text(message)
                            except Exception:
                                break
                    except websockets.ConnectionClosed:
                        logger.info("云端 WebSocket 已断开")
                    except Exception as e:
                        logger.error(f"云端 WebSocket 转发异常：{e}")

                # 启动本地 → 云端 的消息转发（心跳）
                async def forward_local_to_cloud():
                    try:
                        while True:
                            data = await websocket.receive_text()
                            try:
                                msg = json.loads(data)
                                if msg.get("type") == "ping":
                                    await cloud_ws.send(json.dumps({
                                        "type": "ping",
                                        "task_id": task_id,
                                    }))
                            except json.JSONDecodeError:
                                await cloud_ws.send(data)
                    except WebSocketDisconnect:
                        logger.info("本地前端 WebSocket 断开")
                    except Exception as e:
                        logger.error(f"本地 WebSocket 接收异常：{e}")

                # 同时运行双向转发
                import asyncio
                try:
                    await asyncio.gather(
                        forward_cloud_to_local(),
                        forward_local_to_cloud(),
                    )
                except Exception:
                    pass

        except websockets.exceptions.ConnectTimeout:
            logger.error(f"云端 WebSocket 连接超时：{ws_url}")
            await websocket.send_json({
                "type": "error",
                "message": "云端服务暂时不可用，进度推送已降级为 HTTP 轮询模式",
                "fallback": "http_poll",
                "task_id": task_id,
            })
        except websockets.exceptions.InvalidStatus as e:
            logger.error(f"云端 WebSocket 握手失败：{e}")
            await websocket.send_json({
                "type": "error",
                "message": "云端 WebSocket 服务不可用，请使用 HTTP 轮询查询进度",
                "fallback": "http_poll",
                "task_id": task_id,
            })
        except Exception as e:
            logger.error(f"云端 WebSocket 连接异常：{e}")
            await websocket.send_json({
                "type": "error",
                "message": f"连接云端进度服务失败，请稍后重试",
                "fallback": "http_poll",
                "task_id": task_id,
            })

    except WebSocketDisconnect:
        logger.info(f"云端进度 WebSocket 正常断开：task_id={task_id}")
    except Exception as e:
        logger.exception(f"云端进度 WebSocket 异常：task_id={task_id}")
    finally:
        logger.info(f"云端进度 WebSocket 已关闭：task_id={task_id}")


@router.get(
    "/health",
    summary="云端 API 连通性检查",
    description="检查云端 API 网关是否可达。",
)
async def cloud_health() -> dict:
    """检查云端 API 网关连通性。"""
    status_code, body = await _cloud_http_get("/health")
    if status_code == 200:
        return {
            "success": True,
            "message": "云端服务正常",
            "data": body,
        }
    return {
        "success": False,
        "message": f"云端服务不可达（HTTP {status_code}）",
        "data": {"status_code": status_code, "body": body},
    }


# ================================================================
# 辅助函数
# ================================================================
def create_forward_token(user_id: str) -> str:
    """
    为云端 API 转发请求生成 Token。

    使用本地 JWT 密钥生成，与云端 API 网关共享同一密钥（
    通过环境变量 NEXUS_JWT_SECRET 配置）。
    云端网关会验证此 Token 来识别用户身份。
    """
    from core.auth import create_access_token
    return create_access_token(str(user_id))