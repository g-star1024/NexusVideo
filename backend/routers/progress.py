"""
NexusVideo Backend - 进度 WebSocket 路由
============================================================
架构位置：routers/progress.py
被 local_server.py 注册为 /progress/* 路由。

白皮书 5.2 节 + 设计系统规范 7.3 节：
    生成进度用"文案化"描述，而非百分比数字。

前端交互流程：
    1. 前端 POST /generate 后获得 task_id
    2. 前端 WebSocket 连接 GET /progress/ws?task_id=xxx
    3. 后端监听 ComfyUI WebSocket 进度事件，翻译为文案后推送到此通道
    4. 前端在 2.5-3 秒间隔内轮换 phase_messages 中的文案显示

推送消息格式（见 comfyui_ws.py 的 _build_progress_payload）：
    {
        "task_id": "...",
        "progress": 0-100,
        "phase": 1-4,
        "message": "正在渲染细节…",
        "phase_messages": ["正在渲染细节…", "画面逐渐成形…", ...],
        "estimated_text": "预计还需 30 秒",
        "output_url": "http://..." (可选)
    }

注意：
    - 前端断连时自动清理注册，不会内存泄漏
    - 同一 task_id 可注册多个前端连接（如多窗口场景）
    - 如果前端连接时任务已完成，直接推送完成状态后关闭
"""

import json
import time
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from loguru import logger

from core.comfyui_ws import ws_listener
from core.task_manager import task_manager

router = APIRouter(prefix="/progress", tags=["进度"])


# ================================================================
# WebSocket 端点
# ================================================================
@router.websocket("/ws")
async def progress_websocket(websocket: WebSocket):
    """
    进度 WebSocket 端点。

    前端通过 query 参数传入 task_id：
        ws://localhost:9881/progress/ws?task_id=xxx

    连接后：
        1. 验证 task_id 是否存在于任务表中
        2. 注册前端连接
        3. 如果有当前进度，立即推送一次
        4. 进入监听循环，等待前端可能的指令（当前版本仅推送）

    断开时：
        1. 自动取消注册
        2. 日志记录

    注意：
        前端连接时不需要发送任何消息，后端会主动推送。
        如果任务尚未开始或已完成，仍然建立连接，
        后续任务开始/完成时会自动推送。
    """
    task_id: Optional[str] = websocket.query_params.get("task_id")

    if not task_id:
        await websocket.close(code=4003, reason="缺少 task_id 参数")
        return

    # 验证任务存在
    record = task_manager.get_task_status(task_id)
    if record is None:
        await websocket.close(code=4004, reason=f"任务不存在：{task_id}")
        return

    # 接受 WebSocket 连接
    await websocket.accept()
    logger.info(f"进度 WebSocket 连接：task_id={task_id}")

    # 注册到 comfyui_ws 管理器
    await ws_listener.register_frontend(task_id, websocket)

    try:
        # 立即推送当前进度（如果任务已在进行中）
        _push_current_state(websocket, task_id, record)

        # 进入监听循环
        # 当前版本仅做心跳维持，所有进度推送由 comfyui_ws 主动广播
        while True:
            # 等待前端消息（前端不需要发送，此循环仅维持连接）
            # 使用 30 秒超时来检测前端是否异常断开
            try:
                raw = await websocket.receive_text()
                # 前端可发送 JSON 来控制（如请求重新推送）
                try:
                    msg = json.loads(raw)
                    action = msg.get("action")
                    if action == "ping":
                        await websocket.send_text(json.dumps({
                            "type": "pong",
                            "task_id": task_id,
                            "timestamp": time.time(),
                        }, ensure_ascii=False))
                    elif action == "push_now":
                        # 手动请求推送当前状态
                        current_record = task_manager.get_task_status(task_id)
                        if current_record:
                            _push_current_state(websocket, task_id, current_record)
                except json.JSONDecodeError:
                    logger.warning(f"收到非法 JSON：{raw}")
            except WebSocketDisconnect:
                logger.info(f"进度 WebSocket 正常断开：task_id={task_id}")
                break
            except Exception as e:
                logger.warning(f"进度 WebSocket 异常：task_id={task_id}, error={e}")
                break

    finally:
        # 确保断开时清理
        await ws_listener.unregister_frontend(task_id, websocket)
        logger.info(f"进度 WebSocket 已断开并清理：task_id={task_id}")


# ================================================================
# 辅助函数
# ================================================================
def _push_current_state(
    ws: WebSocket,
    task_id: str,
    record,
) -> None:
    """
    将当前任务状态推送为一条进度消息。

    前端首次连接时可能错过之前的文案化推送，
    此处立即发送一次当前状态。
    """
    try:
        from core.progress_translator import ProgressTranslator

        translator = ProgressTranslator()
        phase = translator.get_phase(record.progress)
        message = translator.translate(record.progress)
        phase_messages = translator.get_phase_messages(record.progress)
        estimated_text = translator.get_estimated_text(record.progress, 0)

        payload = {
            "task_id": task_id,
            "progress": round(record.progress, 1),
            "phase": phase,
            "message": message,
            "phase_messages": phase_messages,
            "estimated_text": estimated_text,
            "status": str(record.status),
        }
        if record.output_url:
            payload["output_url"] = record.output_url

        asyncio_safe_send(ws, json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        logger.warning(f"推送当前状态失败：task_id={task_id}, error={e}")


def asyncio_safe_send(ws: WebSocket, data: str) -> None:
    """
    安全地发送 WebSocket 消息。

    注意：在 FastAPI WebSocket 端点中，send 是同步的，
    但底层可能需要事件循环。此处仅作转发。
    """
    try:
        ws.send_text(data)
    except Exception:
        pass


# ================================================================
# HTTP 辅助端点（可选，供前端轮询兜底）
# ================================================================
@router.get("/status/{task_id}")
async def progress_status_http(task_id: str):
    """
    进度 HTTP 查询端点（WebSocket 的兜底方案）。

    前端如果 WebSocket 连接失败，可退回到此端点轮询。
    返回当前任务状态和文案化进度。
    """
    record = task_manager.get_task_status(task_id)

    if record is None:
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "error_code": "12001",
                "message": "任务不存在",
                "detail": {"task_id": task_id},
            },
        )

    from core.progress_translator import ProgressTranslator

    translator = ProgressTranslator()
    phase = translator.get_phase(record.progress)
    message = translator.translate(record.progress)
    phase_messages = translator.get_phase_messages(record.progress)
    estimated_text = translator.get_estimated_text(record.progress, 0)

    return {
        "success": True,
        "task_id": task_id,
        "status": str(record.status),
        "progress": round(record.progress, 1),
        "phase": phase,
        "message": message,
        "phase_messages": phase_messages,
        "estimated_text": estimated_text,
        "stage_message": record.stage_message,
        "output_url": record.output_url,
        "error": record.error,
        "error_code": record.error_code,
    }