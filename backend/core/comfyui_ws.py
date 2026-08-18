from __future__ import annotations

"""
NexusVideo Backend - ComfyUI WebSocket 连接管理器
============================================================
架构位置：core/comfyui_ws.py
P1 阶段的核心实时进度通道。

数据流（白皮书 4.2 节翻译官架构的实时分支）：
    前端 Tauri → FastAPI POST /generate → 提交 /prompt → task_manager 登记任务
        ↓
    ComfyUI WebSocket (ws://127.0.0.1:8188/ws)
        ↓ 解析 progress / executing / executed 事件
    comfyui_ws.py 内部
        ↓ 文案化翻译（progress_translator）
        ↓ 广播到注册的 WebSocket 端点（routers/progress.py）
    前端进度文案化组件

模块职责：
    1. 连接 ComfyUI WebSocket（自动重连）
    2. 解析 progress 事件（value/max → 0-100%）
    3. 解析 executing 事件（当前节点 ID）
    4. 解析 executed 事件（输出路径）
    5. 维护 task_id → client_id 映射
    6. 注册前端 WebSocket 推送通道
    7. 将原始进度 → 文案化消息 → 广播

ComfyUI WebSocket 消息格式参考：
    {
        "type": "progress",
        "data": { "value": 15, "max": 20, "prompt_id": "xxx" }
    }
    {
        "type": "executing",
        "data": { "node": "8", "prompt_id": "xxx" }
    }
    {
        "type": "executed",
        "data": { "node": "9", "prompt_id": "xxx", "output": {...} }
    }
"""

from fastapi import WebSocket
import asyncio
import json
import uuid
from typing import Any, Callable, Awaitable, Optional

import websockets
from loguru import logger

from config import settings
from core.progress_translator import ProgressTranslator
from core.task_manager import task_manager


# ================================================================
# WebSocket 消息类型常量
# ================================================================
class WSMessageType:
    STATUS = "status"
    EXECUTION_START = "execution_start"
    EXECUTION_SUCCESS = "execution_success"
    EXECUTION_ERROR = "execution_error"
    EXECUTION_INTERRUPTED = "execution_interrupted"
    EXECUTING = "executing"           # 某节点开始执行
    EXECUTED = "executed"             # 某节点执行完成
    PROGRESS = "progress"             # 采样进度（步数）
    RESULT = "result"


# ================================================================
# 前端推送消息格式
# ================================================================
def _build_progress_payload(
    task_id: str,
    progress_pct: float,
    phase: int,
    message: str,
    phase_messages: list[str],
    estimated_text: str,
    output_url: Optional[str] = None,
) -> dict:
    """
    构建推送给前端 WebSocket 端点的标准消息格式。

    推送格式：
        {
            "task_id": "...",
            "progress": 0-100,
            "phase": 1-4,
            "message": "正在渲染细节…",
            "phase_messages": ["正在渲染细节…", "画面逐渐成形…", ...],
            "estimated_text": "预计还需 30 秒",
            "output_url": "http://..." (可选，完成时)
        }
    """
    payload: dict[str, Any] = {
        "task_id": task_id,
        "progress": round(progress_pct, 1),
        "phase": phase,
        "message": message,
        "phase_messages": phase_messages,
        "estimated_text": estimated_text,
    }
    if output_url:
        payload["output_url"] = output_url
    return payload


# ================================================================
# 全局 WebSocket 连接管理器
# ================================================================
class ComfyUIWebSocketListener:
    """
    ComfyUI WebSocket 进度监听器。

    职责：
        1. 与 ComfyUI 维持一条长连接（自动重连）
        2. 实时解析 progress / executing / executed 事件
        3. 将原始进度翻译为文案化消息
        4. 广播到注册的前端 WebSocket 通道

    连接策略：
        - URL: ws://127.0.0.1:8188/ws?clientId={client_id}
        - 断开后 3 秒重连
        - 异常后 5 秒重连
        - ping_interval=20s 保活
    """

    def __init__(self) -> None:
        self._ws = None
        self._client_id: str = str(uuid.uuid4())
        self._running = False

        # ============================================================
        # 回调注册表（内部扩展点）
        # ============================================================
        self._callbacks: dict[str, list[Callable]] = {}

        # ============================================================
        # task_id → client_id 映射
        # 当 task_manager 提交任务后，通过此映射将前端任务 ID
        # 与 ComfyUI 的 client_id 关联，用于事件路由。
        # ============================================================
        self._task_client_map: dict[str, str] = {}

        # ============================================================
        # 前端 WebSocket 通道
        # 每个前端连接注册一个 {task_id → websocket} 对。
        # 广播时遍历所有注册的通道，将文案化消息推送过去。
        # ============================================================
        self._frontends: dict[str, set[WebSocket]] = {}

        # ============================================================
        # 前端通道的推送锁（防止并发写 WebSocket）
        # ============================================================
        self._push_lock = asyncio.Lock()

        # ============================================================
        # 每个 task_id 独立的翻译器实例
        # 确保"同一条不连续重复"在每个连接中独立生效。
        # ============================================================
        self._translators: dict[str, ProgressTranslator] = {}

        # ============================================================
        # 每个 task_id 的估计总时长（秒）
        # 用于副文案（"预计还需 XX 秒"）。默认值来自 settings，
        # 实际可由前端传入或根据历史数据调整。
        # ============================================================
        self._task_estimated_time: dict[str, int] = {}

    # ================================================================
    # 回调注册（内部扩展点）
    # ================================================================
    def on(self, msg_type: str, callback: Callable[[dict], Awaitable[None]]) -> None:
        """注册消息回调函数。"""
        self._callbacks.setdefault(msg_type, []).append(callback)

    # ================================================================
    # task_id ↔ client_id 映射
    # ================================================================
    def register_task(self, task_id: str, client_id: str) -> None:
        """
        注册 task_id 与 client_id 的映射。

        在 task_manager 提交 /prompt 后调用，
        使后续的 WebSocket 事件能正确路由到前端。

        Args:
            task_id: 任务 ID（= ComfyUI prompt_id）
            client_id: 提交 /prompt 时使用的 client_id
        """
        self._task_client_map[task_id] = client_id
        logger.debug(f"注册任务映射：task_id={task_id}, client_id={client_id}")

    def unregister_task(self, task_id: str) -> None:
        """任务完成或失败后清理映射。"""
        self._task_client_map.pop(task_id, None)
        self._translators.pop(task_id, None)
        self._task_estimated_time.pop(task_id, None)
        self._frontends.pop(task_id, None)
        logger.debug(f"清理任务映射：task_id={task_id}")

    def set_estimated_time(self, task_id: str, total_seconds: int) -> None:
        """设置任务的预估总时长（秒），用于副文案。"""
        self._task_estimated_time[task_id] = total_seconds

    # ================================================================
    # 前端 WebSocket 通道管理
    # ================================================================
    async def register_frontend(self, task_id: str, ws: WebSocket) -> None:
        """
        注册前端 WebSocket 连接。

        当前端 GET /progress/ws?task_id=xxx 时，
        routers/progress.py 调用此方法注册通道。
        """
        self._frontends.setdefault(task_id, set()).add(ws)
        logger.debug(f"注册前端通道：task_id={task_id}")

    async def unregister_frontend(self, task_id: str, ws: WebSocket) -> None:
        """前端 WebSocket 断开时取消注册。"""
        frontends = self._frontends.get(task_id)
        if frontends:
            frontends.discard(ws)
            if not frontends:
                del self._frontends[task_id]
        logger.debug(f"取消前端通道：task_id={task_id}")

    # ================================================================
    # 连接与监听
    # ================================================================
    async def connect(self) -> None:
        """
        连接 ComfyUI WebSocket，进入消息监听循环。

        URL: ws://127.0.0.1:8188/ws?clientId={client_id}
        """
        url = f"{settings.comfyui_ws_url}?clientId={self._client_id}"
        logger.info(f"连接 ComfyUI WebSocket：{url}")

        self._running = True
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    self._ws = ws
                    logger.info("ComfyUI WebSocket 已连接")
                    await self._listen(ws)
            except websockets.ConnectionClosed:
                logger.warning("ComfyUI WebSocket 连接断开，3秒后重连...")
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"WebSocket 连接异常：{e}，5秒后重连...")
                await asyncio.sleep(5)

    async def _listen(self, ws) -> None:
        """消息接收与分发循环。"""
        async for raw_msg in ws:
            try:
                msg = json.loads(raw_msg)
                msg_type = msg.get("type")
                msg_data = msg.get("data", {})

                logger.debug(f"WS 收到消息：type={msg_type}, data={msg_data}")

                # 1. 分发给注册的通用回调（内部扩展点）
                for cb in self._callbacks.get(msg_type, []):
                    await cb(msg_data)
                for cb in self._callbacks.get("*", []):
                    await cb(msg)

                # 2. 内置事件处理：progress / executing / executed
                if msg_type == WSMessageType.PROGRESS:
                    await self._handle_progress(msg_data)
                elif msg_type == WSMessageType.EXECUTING:
                    await self._handle_executing(msg_data)
                elif msg_type == WSMessageType.EXECUTED:
                    await self._handle_executed(msg_data)

            except json.JSONDecodeError:
                logger.warning(f"WebSocket 消息 JSON 解析失败：{raw_msg}")
            except Exception as e:
                logger.exception(f"WebSocket 消息处理异常：{e}")

    # ================================================================
    # progress 事件处理
    # ================================================================
    async def _handle_progress(self, data: dict) -> None:
        """
        处理 progress 事件。

        原始数据：
            { "value": 15, "max": 20, "prompt_id": "xxx" }

        处理步骤：
            1. 提取 prompt_id（= task_id）
            2. 计算进度百分比 = value / max * 100
            3. 通过 task_manager 更新 TaskRecord 中的 progress
            4. 文案化翻译（progress_translator.translate）
            5. 广播到所有注册的前端 WebSocket 通道
        """
        prompt_id = data.get("prompt_id", "")
        value = data.get("value", 0)
        max_val = data.get("max", 1)

        if not prompt_id:
            logger.warning(f"progress 事件缺少 prompt_id: {data}")
            return

        # 计算进度百分比
        progress_pct = (value / max_val) * 100 if max_val > 0 else 0.0
        progress_pct = min(100.0, progress_pct)

        logger.info(f"任务 {prompt_id} 进度更新：{value}/{max_val} = {progress_pct:.1f}%")

        # 更新 task_manager 中的进度
        record = task_manager.get_task_status(prompt_id)
        if record:
            record.progress = round(progress_pct, 1)

        # 文案化翻译
        translator = self._translators.setdefault(
            prompt_id, ProgressTranslator()
        )
        phase = translator.get_phase(progress_pct)
        message = translator.translate(progress_pct)
        phase_messages = translator.get_phase_messages(progress_pct)

        # 获取副文案（预计剩余时间）
        estimated_seconds = self._task_estimated_time.get(prompt_id, 0)
        estimated_text = translator.get_estimated_text(
            progress_pct, estimated_seconds
        )

        # 广播到前端
        await self._broadcast(prompt_id, progress_pct, phase, message, phase_messages, estimated_text)

        # 更新 stage_message（供前端轮询 /task/{id} 时使用）
        if record:
            record.stage_message = message

    # ================================================================
    # executing 事件处理
    # ================================================================
    async def _handle_executing(self, data: dict) -> None:
        """
        处理 executing 事件。

        原始数据：
            { "node": "8", "prompt_id": "xxx" }

        用于记录当前正在执行的节点 ID，辅助错误定位。
        """
        prompt_id = data.get("prompt_id", "")
        node_id = data.get("node", "")

        logger.info(f"任务 {prompt_id} 正在执行节点：{node_id}")

        # 更新 task_manager 中的当前节点（用于错误定位）
        record = task_manager.get_task_status(prompt_id)
        if record and node_id:
            # 将节点 ID 记录到 error 字段的上下文（如果有的话）
            # 这里暂不修改 TaskRecord，仅用于日志追踪
            logger.debug(f"节点执行：task_id={prompt_id}, node={node_id}")

    # ================================================================
    # executed 事件处理
    # ================================================================
    async def _handle_executed(self, data: dict) -> None:
        """
        处理 executed 事件。

        原始数据：
            {
                "node": "9",
                "prompt_id": "xxx",
                "output": { "videos": [{"filename": "...", ...}] }
            }

        当所有节点执行完成时，提取输出路径并广播最终消息。
        """
        prompt_id = data.get("prompt_id", "")
        node_id = data.get("node", "")
        output = data.get("output", {})

        if not prompt_id:
            return

        logger.info(f"任务 {prompt_id} 节点执行完成：node={node_id}")

        # 提取输出 URL
        output_url = self._extract_output_url(output)

        # 广播完成消息
        translator = self._translators.get(prompt_id, ProgressTranslator())
        message = translator.translate(100.0)  # "生成完成！"
        phase_messages = translator.get_phase_messages(95.0)  # 阶段四的文案列表
        estimated_text = ""

        await self._broadcast(
            prompt_id, 100.0, 4, message, phase_messages, estimated_text, output_url
        )

    @staticmethod
    def _extract_output_url(output: dict) -> Optional[str]:
        """从 executed 事件的 output 字段中提取视频文件 URL。"""
        for video_key in ("videos", "gifs", "images"):
            if video_key in output and output[video_key]:
                file_info = output[video_key][0]
                filename = file_info.get("filename", "")
                subfolder = file_info.get("subfolder", "")
                if filename:
                    return (
                        f"http://{settings.comfyui_host}:{settings.comfyui_port}"
                        f"/view?filename={filename}&subfolder={subfolder}&type=output"
                    )
        return None

    # ================================================================
    # 广播到前端 WebSocket 通道
    # ================================================================
    async def _broadcast(
        self,
        task_id: str,
        progress_pct: float,
        phase: int,
        message: str,
        phase_messages: list[str],
        estimated_text: str,
        output_url: Optional[str] = None,
    ) -> None:
        """
        将文案化进度消息广播到所有注册的前端 WebSocket 通道。

        使用 asyncio.Lock 防止并发写 WebSocket 导致消息交错。
        """
        async with self._push_lock:
            frontends = self._frontends.get(task_id)
            if not frontends:
                logger.debug(f"无前端通道可推送：task_id={task_id}")
                return

            payload = _build_progress_payload(
                task_id=task_id,
                progress_pct=progress_pct,
                phase=phase,
                message=message,
                phase_messages=phase_messages,
                estimated_text=estimated_text,
                output_url=output_url,
            )
            payload_json = json.dumps(payload, ensure_ascii=False)

            # 逐个推送，失败的连接自动丢弃
            dead_connections: set[WebSocket] = set()
            for ws in frontends:
                try:
                    await ws.send(payload_json)
                except Exception as e:
                    logger.warning(f"推送失败，移除连接：task_id={task_id}, error={e}")
                    dead_connections.add(ws)

            # 清理断开的连接
            for ws in dead_connections:
                frontends.discard(ws)

    # ================================================================
    # 停止监听
    # ================================================================
    async def stop(self) -> None:
        """停止 WebSocket 监听，断开与 ComfyUI 的连接。"""
        self._running = False
        if self._ws:
            await self._ws.close()
            logger.info("ComfyUI WebSocket 已断开")


# ================================================================
# 全局单例
# ================================================================
ws_listener = ComfyUIWebSocketListener()