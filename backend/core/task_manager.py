"""
NexusVideo Backend - 任务管理器
============================================================
管理从提交到完成的完整任务生命周期。

架构位置：core/task_manager.py
被 generate router 调用（提交任务），被 task router 调用（查询状态）。

职责：
  1. 提交工作流到 ComfyUI，获取 prompt_id
  2. 轮询 /history/{prompt_id} 获取任务结果
  3. 超时控制与自动重试
  4. 进度文案化（白皮书 5.2：不显示"45%"，显示"正在构思画面..."）
  5. 内存任务表（P0），P2 可迁移到 Redis
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx
import httpcore
from loguru import logger

from config import settings
from core.comfyui_client import comfyui_client
from core.workflow_translator import translator
from core.skill_registry import skill_registry, SKILL_MODE_TO_GEN
from models.schemas import GenerateRequest, TaskStatus, GenerationMode, SkillMode
from exceptions import (
    ComfyUINotRunningError,
    ComfyUITimeoutError,
    ComfyUINodeError,
    ComfyUIOOMError,
    TaskQueueFullError,
)


# ================================================================
# 进度阶段文案（白皮书 5.2 节）
# ================================================================
# 将 ComfyUI 的技术进度映射为"人类能听懂"的文案
STAGE_MESSAGES: list[tuple[float, str]] = [
    (0.0,   "正在理解你的创意..."),
    (10.0,  "正在构思画面..."),
    (25.0,  "画面已成型，正在精雕细琢..."),
    (50.0,  "正在逐帧渲染视频..."),
    (75.0,  "即将完成，请稍候..."),
    (90.0,  "最后润色中..."),
    (100.0, "生成完成！"),
]


def get_stage_message(progress: float) -> str:
    """根据进度百分比返回对应的阶段文案。"""
    for threshold, msg in STAGE_MESSAGES:
        if progress <= threshold:
            return msg
    return STAGE_MESSAGES[-1][1]


# ================================================================
# 任务记录
# ================================================================
@dataclass
class TaskRecord:
    """单个任务的完整记录（内存存储，P2 迁移 Redis）。"""
    task_id: str                          # = ComfyUI prompt_id
    prompt: str                           # 用户输入
    mode: GenerationMode                  # 生成模式
    seed: int                             # 实际使用的种子
    status: TaskStatus = TaskStatus.QUEUED
    progress: float = 0.0
    stage_message: str = "正在理解你的创意..."
    output_url: str | None = None
    output_filename: str | None = None
    error: str | None = None
    error_code: str | None = None
    skill_id: str | None = None     # 若由 Skill Registry 派发，记录技能 id
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    retry_count: int = 0
    degradation_level: int = 0            # 降级级别（0=未降级）


# ================================================================
# 任务管理器
# ================================================================
class TaskManager:
    """
    任务生命周期管理。

    核心方法：
        submit_and_track(request) → task_id    提交并异步跟踪
        get_task_status(task_id)  → TaskRecord 查询任务状态
    """

    def __init__(self):
        # 任务存储：{ task_id: TaskRecord }
        self._tasks: dict[str, TaskRecord] = {}
        # 活跃任务计数（用于队列满判断）
        self._active_count = 0
        # 活跃任务锁
        self._lock = asyncio.Lock()

    # ================================================================
    # 提交任务
    # ================================================================
    async def submit_and_track(
        self, request: GenerateRequest
    ) -> tuple[str, int]:
        """
        翻译参数 → 提交 ComfyUI → 后台跟踪 → 返回 task_id

        返回：(task_id, resolved_seed)

        流程：
            1. 检查队列容量
            2. 翻译官：request → workflow JSON
            3. 提交到 ComfyUI /prompt，获取 prompt_id
            4. 创建 TaskRecord
            5. 启动后台跟踪协程（轮询 /history）
        """
        # Step 1: 队列容量检查
        async with self._lock:
            if self._active_count >= settings.max_concurrent_tasks:
                raise TaskQueueFullError(settings.max_concurrent_tasks)
            self._active_count += 1

        try:
            # Step 2: 翻译参数为工作流
            workflow, resolved_seed = translator.translate(request)

            # Step 3: 提交到 ComfyUI
            # 生成 client_id 用于 WebSocket 关联（P1 阶段启用）
            client_id = str(uuid.uuid4())
            try:
                result = await comfyui_client.submit_prompt(workflow, client_id)
            except (httpx.ConnectError, httpx.ConnectTimeout,
                    httpcore.ConnectError, OSError) as e:
                logger.warning(
                    f"ComfyUI 连接失败，异常类型={type(e).__name__}，消息={e}"
                )
                raise ComfyUINotRunningError(detail={
                    "comfyui_url": settings.comfyui_base_url,
                    "exception_type": type(e).__name__,
                    "exception_message": str(e),
                    "hint": "本地 ComfyUI 服务未启动或无法连接，请检查服务状态",
                })
            task_id = result["prompt_id"]

            # Step 4: 创建任务记录
            record = TaskRecord(
                task_id=task_id,
                prompt=request.prompt,
                mode=request.mode,
                seed=resolved_seed,
                status=TaskStatus.QUEUED,
            )
            self._tasks[task_id] = record
            logger.info(f"任务已提交：task_id={task_id}, seed={resolved_seed}")

            # Step 5: 启动后台跟踪协程
            asyncio.create_task(self._track_task(task_id, workflow, request))

            return task_id, resolved_seed

        except Exception:
            # 提交失败，释放队列槽位
            async with self._lock:
                self._active_count -= 1
            raise

    # ================================================================
    # 提交技能预构建工作流（Skill Registry 专用）
    # ================================================================
    async def submit_prepared_workflow(
        self,
        workflow: dict[str, Any],
        skill_id: str,
        params: dict[str, Any],
        seed: int | None = None,
    ) -> tuple[str, int]:
        """
        提交已由 SkillRegistry.build_workflow 产好的工作流。

        与 submit_and_track 完全复用的部分：
          - 队列容量检查（TaskQueueFullError）
          - submit_prompt → 获取 prompt_id
          - ConnectError/ConnectTimeout → ComfyUINotRunningError（上一轮修复的错误路径）
          - 创建 TaskRecord → 启动 _track_task（OOM 自动降级重试链路一致）

        不同点：
          - 跳过 translator.translate()，因为 workflow 已由 SkillRegistry 构建
          - request=None（技能无需 GenerateRequest，降级重试时亦不依赖 request）

        返回：(task_id, resolved_seed)
        """
        # Step 1: 队列容量检查（复用既有逻辑）
        async with self._lock:
            if self._active_count >= settings.max_concurrent_tasks:
                raise TaskQueueFullError(settings.max_concurrent_tasks)
            self._active_count += 1

        try:
            # 种子：优先用调用方传入的（build_workflow 已解析），否则兜底随机
            resolved_seed = seed if seed is not None else random.randint(0, 2**32 - 1)

            # Step 2: 提交到 ComfyUI（复用既有连接错误捕获 → 明确业务错误码）
            client_id = str(uuid.uuid4())
            try:
                result = await comfyui_client.submit_prompt(workflow, client_id)
            except (httpx.ConnectError, httpx.ConnectTimeout,
                    httpcore.ConnectError, OSError) as e:
                logger.warning(
                    f"ComfyUI 连接失败，异常类型={type(e).__name__}，消息={e}"
                )
                raise ComfyUINotRunningError(detail={
                    "comfyui_url": settings.comfyui_base_url,
                    "exception_type": type(e).__name__,
                    "exception_message": str(e),
                    "hint": "本地 ComfyUI 服务未启动或无法连接，请检查服务状态",
                })
            task_id = result["prompt_id"]

            # Step 3: 创建任务记录（mode 依据技能 manifest.mode 映射回 GenerationMode）
            manifest = skill_registry.get_skill(skill_id)
            gen_mode_value = (
                SKILL_MODE_TO_GEN.get(manifest.mode, GenerationMode.TXT2VIDEO)
                if manifest is not None
                else GenerationMode.TXT2VIDEO
            )
            record = TaskRecord(
                task_id=task_id,
                prompt=params.get("prompt", "") or "",
                mode=GenerationMode(gen_mode_value),
                seed=resolved_seed,
                status=TaskStatus.QUEUED,
                skill_id=skill_id,
            )
            self._tasks[task_id] = record
            logger.info(
                f"技能任务已提交：skill={skill_id}, task_id={task_id}, seed={resolved_seed}"
            )

            # Step 4: 启动后台跟踪协程（request=None，降级重试路径兼容）
            asyncio.create_task(self._track_task(task_id, workflow, request=None))

            return task_id, resolved_seed

        except Exception:
            # 提交失败，释放队列槽位
            async with self._lock:
                self._active_count -= 1
            raise

    # ================================================================
    # 后台任务跟踪
    # ================================================================
    async def _track_task(
        self,
        task_id: str,
        workflow: dict[str, Any],
        request: GenerateRequest | None = None,
    ) -> None:
        """
        后台轮询 ComfyUI /history/{prompt_id}，更新任务状态。

        这是 P0 阶段的轮询实现。P1 阶段将替换为 WebSocket 实时推送
        （core/comfyui_ws.py），实现更精准的进度反馈。

        超时与重试策略：
            - 单任务超时：task_timeout 秒
            - OOM 自动降级重试：最多 max_retry 次
        """
        record = self._tasks[task_id]
        record.status = TaskStatus.RUNNING
        record.stage_message = "正在理解你的创意..."

        deadline = time.time() + settings.task_timeout

        try:
            while time.time() < deadline:
                # 轮询 /history
                history = await comfyui_client.get_history(task_id)

                if history is None:
                    # 任务还在排队/执行中，更新模拟进度
                    elapsed = time.time() - record.created_at
                    # 模拟进度曲线（P0 阶段，P1 替换为真实 WebSocket 进度）
                    estimated_progress = min(
                        90.0,
                        (elapsed / settings.task_timeout) * 80.0 + 10.0,
                    )
                    record.progress = round(estimated_progress, 1)
                    record.stage_message = get_stage_message(record.progress)
                    await asyncio.sleep(settings.task_poll_interval)
                    continue

                # 任务已完成，解析结果
                status_info = history.get("status", {})
                status_str = status_info.get("status_str", "")

                if status_str == "success":
                    # 成功：提取输出文件
                    self._extract_output(task_id, history)
                    record.status = TaskStatus.SUCCESS
                    record.progress = 100.0
                    record.stage_message = "生成完成！"
                    record.completed_at = time.time()
                    logger.info(f"任务成功完成：{task_id}")

                elif status_str == "error":
                    # 失败：检查是否 OOM，尝试降级重试
                    error_msg = self._extract_error(history)
                    is_oom = self._is_oom_error(error_msg)

                    if is_oom and record.retry_count < settings.max_retry:
                        # OOM 自动降级重试
                        record.retry_count += 1
                        record.degradation_level = record.retry_count
                        logger.warning(
                            f"检测到显存不足，执行降级重试 "
                            f"(第 {record.retry_count} 次，Level {record.degradation_level})"
                        )
                        await self._retry_with_degradation(task_id, workflow, request)
                        return  # 重试协程接管跟踪
                    else:
                        record.status = TaskStatus.FAILED
                        record.error = error_msg
                        record.error_code = (
                            "11004" if is_oom else "11005"
                        )
                        record.completed_at = time.time()
                        logger.error(f"任务失败：{task_id}, error={error_msg}")

                return

            # 超时
            record.status = TaskStatus.TIMEOUT
            record.error = f"任务超时（{settings.task_timeout}秒）"
            record.error_code = "12002"
            record.completed_at = time.time()
            logger.error(f"任务超时：{task_id}")
            # 尝试中断 ComfyUI 当前任务
            try:
                await comfyui_client.interrupt()
            except Exception:
                pass

        except (httpx.ConnectError, httpx.ConnectTimeout, httpcore.ConnectError, OSError):
            record.status = TaskStatus.FAILED
            record.error = "ComfyUI 连接失败，推理引擎可能已崩溃"
            record.error_code = "11001"
            record.completed_at = time.time()

        except Exception as e:
            record.status = TaskStatus.FAILED
            record.error = str(e)
            record.error_code = "10001"
            record.completed_at = time.time()
            logger.exception(f"任务跟踪异常：{task_id}")

        finally:
            async with self._lock:
                self._active_count -= 1

    # ================================================================
    # 提取输出文件
    # ================================================================
    def _extract_output(self, task_id: str, history: dict) -> None:
        """从 ComfyUI history 中提取输出文件信息。"""
        record = self._tasks[task_id]
        outputs = history.get("outputs", {})

        for node_id, node_output in outputs.items():
            # 优先查找视频输出
            for video_key in ("videos", "gifs", "images"):
                if video_key in node_output and node_output[video_key]:
                    file_info = node_output[video_key][0]
                    filename = file_info.get("filename", "")
                    subfolder = file_info.get("subfolder", "")
                    if filename:
                        record.output_filename = filename
                        record.output_url = (
                            f"http://{settings.comfyui_host}:{settings.comfyui_port}"
                            f"/view?filename={filename}&subfolder={subfolder}&type=output"
                        )
                        logger.info(f"输出文件：{filename}")
                        return

    # ================================================================
    # 错误提取与 OOM 判断
    # ================================================================
    @staticmethod
    def _extract_error(history: dict) -> str:
        """从 ComfyUI history 中提取错误信息。"""
        messages = history.get("status", {}).get("messages", [])
        for msg in messages:
            if isinstance(msg, list) and len(msg) >= 2:
                # ComfyUI 错误消息格式：["execution_error", { "node_id": "...", "exception_message": "..." }]
                msg_type, msg_data = msg[0], msg[1]
                if isinstance(msg_data, dict):
                    node_id = msg_data.get("node_id", "?")
                    error = msg_data.get("exception_message", str(msg_data))
                    return f"[节点 {node_id}] {error}"
        return "未知错误（ComfyUI 未返回详细错误信息）"

    @staticmethod
    def _is_oom_error(error_msg: str) -> bool:
        """判断是否为显存不足错误。"""
        oom_keywords = [
            "out of memory",
            "CUDA out of memory",
            "OutOfMemoryError",
            " HIP out of memory",
            "Tried to allocate",
        ]
        error_lower = error_msg.lower()
        return any(kw.lower() in error_lower for kw in oom_keywords)

    # ================================================================
    # 降级重试
    # ================================================================
    async def _retry_with_degradation(
        self,
        task_id: str,
        workflow: dict,
        request: GenerateRequest | None = None,
    ) -> None:
        """OOM 后执行降级策略并重新提交任务。"""
        record = self._tasks[task_id]

        # 应用降级
        degraded_workflow = translator.apply_degradation(
            workflow, level=record.degradation_level
        )

        # 重新提交
        try:
            client_id = str(uuid.uuid4())
            result = await comfyui_client.submit_prompt(degraded_workflow, client_id)
            new_task_id = result["prompt_id"]

            # 迁移记录到新 task_id
            record.task_id = new_task_id
            self._tasks[new_task_id] = record
            del self._tasks[task_id]

            record.status = TaskStatus.RUNNING
            record.stage_message = "正在以优化模式重新生成..."

            # 重新启动跟踪
            asyncio.create_task(
                self._track_task(new_task_id, degraded_workflow, request)
            )
        except Exception as e:
            record.status = TaskStatus.FAILED
            record.error = f"降级重试失败：{e}"
            record.completed_at = time.time()
            async with self._lock:
                self._active_count -= 1

    # ================================================================
    # 查询任务状态
    # ================================================================
    def get_task_status(self, task_id: str) -> TaskRecord | None:
        """查询任务状态（从内存任务表读取）。"""
        return self._tasks.get(task_id)

    # ================================================================
    # 获取活跃任务数
    # ================================================================
    @property
    def active_count(self) -> int:
        return self._active_count


# 全局单例
task_manager = TaskManager()
