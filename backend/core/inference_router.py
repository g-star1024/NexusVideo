"""
NexusVideo Backend - 推理路由抽象层（本地/云端切换）
============================================================
这是白皮书 4.3 节的"商业命脉"模块——本地/云端无感切换。

架构位置：core/inference_router.py
P0 阶段：仅实现 local 模式 + 接口预留
P2 阶段：实现 cloud 模式 + auto 智能路由 + 用户感知路由

设计原则：
  - 上层（task_manager）不感知推理发生在本地还是云端
  - 通过策略模式实现运行时切换
  - auto 模式根据显存自动决策
  - 用户感知路由：付费用户优先云端，免费用户按显存+偏好决策
"""

import asyncio
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger
from config import settings
from models.schemas import InferenceMode

import httpx
import httpcore


# ================================================================
# 用户角色常量
# ================================================================
ROLE_FREE = "free"
ROLE_PAID = "paid"

# 云端 API 地址（从环境变量读取，支持 NEXUS_CLOUD_API_URL）
CLOUD_API_BASE_URL = (
    os.getenv("NEXUS_CLOUD_API_URL")
    or os.getenv("CLOUD_API_BASE_URL")
    or settings.cloud_endpoint
    or "http://localhost:9000"
)


# ================================================================
# 用户上下文数据类
# ================================================================
@dataclass
class UserContext:
    """
    用户上下文（用于路由决策）。

    来源：认证系统（auth.py + user_db.py）。
    在 InferenceRouter 中作为可选参数传入，
    当 None 时按匿名用户处理（走全局 inference_mode）。
    """
    user_id: str = "anonymous"
    role: str = ROLE_FREE          # free | paid
    quota_remaining: int = 0       # 剩余额度
    preference: Optional[str] = None  # "local" | "cloud" | "auto" | None


# ================================================================
# 推理后端抽象基类
# ================================================================
class InferenceBackend(ABC):
    """
    推理后端抽象接口。

    所有推理后端（本地 ComfyUI / 云端 API）都实现此接口，
    上层 task_manager 通过统一接口调用，不感知具体实现。
    """

    @abstractmethod
    async def submit(self, workflow: dict[str, Any]) -> str:
        """提交工作流，返回 task_id。"""
        ...

    @abstractmethod
    async def get_result(self, task_id: str) -> dict[str, Any] | None:
        """查询任务结果。"""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """检测后端是否可用。"""
        ...

    @abstractmethod
    async def get_health(self) -> dict[str, Any]:
        """获取后端健康状态。"""
        ...

    @property
    @abstractmethod
    def mode(self) -> str:
        """返回后端模式标识。"""
        ...


# ================================================================
# 本地推理后端（ComfyUI 便携版）
# ================================================================
class LocalBackend(InferenceBackend):
    """
    本地推理后端：通过 127.0.0.1:8188 与 ComfyUI 便携版通信。

    P0 阶段唯一启用的后端。
    """

    @property
    def mode(self) -> str:
        return "local"

    async def submit(self, workflow: dict[str, Any]) -> str:
        from core.comfyui_client import comfyui_client
        import uuid
        from exceptions import ComfyUINotRunningError
        try:
            result = await comfyui_client.submit_prompt(workflow, str(uuid.uuid4()))
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
        return result["prompt_id"]

    async def get_result(self, task_id: str) -> dict[str, Any] | None:
        from core.comfyui_client import comfyui_client
        return await comfyui_client.get_history(task_id)

    async def is_available(self) -> bool:
        from core.comfyui_client import comfyui_client
        try:
            await comfyui_client.health_check()
            return True
        except Exception:
            return False

    async def get_health(self) -> dict[str, Any]:
        from core.comfyui_client import comfyui_client
        stats = await comfyui_client.health_check()
        devices = stats.get("devices", [])
        gpu_info = devices[0] if devices else {}
        return {
            "mode": "local",
            "available": True,
            "gpu_name": gpu_info.get("name"),
            "gpu_vram_total_mb": gpu_info.get("vram_total", 0) // (1024 * 1024) if gpu_info else 0,
            "gpu_vram_free_mb": gpu_info.get("vram_free", 0) // (1024 * 1024) if gpu_info else 0,
        }


# ================================================================
# 云端推理后端（P2 阶段实现）
# ================================================================
class CloudBackend(InferenceBackend):
    """
    云端推理后端：对接云端 API 网关（inference_gateway.py）。

    P2 阶段实现：
        1. 将工作流参数打包为请求体
        2. POST 到 cloud_api_url /api/v1/generate
        3. 附带 JWT Token 认证
        4. 返回云端 task_id
    """

    def __init__(self, base_url: str = CLOUD_API_BASE_URL):
        self.base_url = base_url
        self._last_health_check = 0.0
        self._last_available = False
        self._health_cache_ttl = 30  # 健康状态缓存 30 秒

    @property
    def mode(self) -> str:
        return "cloud"

    async def submit(self, workflow: dict[str, Any]) -> str:
        """
        提交工作流到云端 API 网关。

        将 ComfyUI 工作流 JSON 中的关键参数（prompt、mode 等）
        提取后打包为云端 API 请求体。
        """
        # 从工作流中提取关键参数
        prompt = self._extract_prompt_from_workflow(workflow)
        mode = self._extract_mode_from_workflow(workflow)

        payload = {
            "mode": mode,
            "prompt": prompt,
            "params": self._extract_params_from_workflow(workflow),
        }

        logger.info(
            f"云端提交：mode={mode}, prompt='{prompt[:60]}...', url={self.base_url}"
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v1/generate",
                    json=payload,
                    headers=self._build_headers(),
                )

                if resp.status_code == 401:
                    logger.error("云端返回 401，JWT Token 无效")
                    raise RuntimeError("云端认证失败，JWT Token 无效或已过期")
                if resp.status_code == 403:
                    logger.error("云端返回 403，额度不足")
                    raise RuntimeError("今日云端生成额度已用尽")
                if resp.status_code == 429:
                    raise RuntimeError("云端请求过于频繁，请稍后重试")
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"云端服务返回错误（HTTP {resp.status_code}）"
                    )

                body = resp.json()
                task_id = body.get("task_id", "")
                logger.info(f"云端提交成功：task_id={task_id}")
                return task_id

        except httpx.TimeoutException:
            raise RuntimeError("云端服务响应超时")
        except httpx.ConnectError:
            raise RuntimeError("无法连接到云端服务")

    async def get_result(self, task_id: str) -> dict[str, Any] | None:
        """查询云端任务结果。"""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/tasks/{task_id}",
                    headers=self._build_headers(),
                )
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 404:
                    return None
                return None
        except Exception:
            return None

    async def is_available(self) -> bool:
        """检测云端服务是否可达。"""
        now = time.time()
        if now - self._last_health_check < self._health_cache_ttl:
            return self._last_available

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/health")
                self._last_available = resp.status_code == 200
                self._last_health_check = now
                return self._last_available
        except Exception:
            self._last_available = False
            self._last_health_check = now
            return False

    async def get_health(self) -> dict[str, Any]:
        """获取云端健康状态。"""
        available = await self.is_available()
        health = {
            "mode": "cloud",
            "available": available,
            "endpoint": self.base_url,
        }
        if available:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{self.base_url}/ready")
                    if resp.status_code == 200:
                        health.update(resp.json())
            except Exception:
                pass
        return health

    # --- 辅助方法 ---
    def _build_headers(self) -> dict:
        """构建云端请求头。"""
        headers = {"Content-Type": "application/json"}
        api_key = settings.cloud_api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    @staticmethod
    def _extract_prompt_from_workflow(workflow: dict) -> str:
        """从 ComfyUI 工作流 JSON 中提取正向 prompt。"""
        for node_id, node_data in workflow.items():
            if isinstance(node_data, dict):
                cls_type = node_data.get("class_type", "")
                if "Conditional" in cls_type or "Conditioning" in cls_type:
                    inputs = node_data.get("inputs", {})
                    if isinstance(inputs, dict):
                        for key in ("text", "conditioning", "prompt"):
                            val = inputs.get(key)
                            if isinstance(val, str) and val.strip():
                                return val
        return ""

    @staticmethod
    def _extract_mode_from_workflow(workflow: dict) -> str:
        """从工作流中提取生成模式。"""
        for node_id, node_data in workflow.items():
            if isinstance(node_data, dict):
                cls_type = node_data.get("class_type", "")
                if "Wan" in cls_type or "AnimateDiff" in cls_type:
                    return "txt2video"
                if "ImageToVideo" in cls_type or "Image" in cls_type:
                    return "img2video"
        return "txt2video"

    @staticmethod
    def _extract_params_from_workflow(workflow: dict) -> dict:
        """从工作流中提取可调参数。"""
        params = {}
        for node_id, node_data in workflow.items():
            if isinstance(node_data, dict):
                inputs = node_data.get("inputs", {})
                if isinstance(inputs, dict):
                    for key in ("seed", "steps", "cfg", "width", "height", "frames"):
                        if key in inputs:
                            params[key] = inputs[key]
        return params


# ================================================================
# 推理路由器（含用户感知智能路由）
# ================================================================
class InferenceRouter:
    """
    推理路由器：根据配置模式 + 用户信息选择本地或云端后端。

    P0：仅 local 模式
    P2：local + cloud + auto（智能路由）+ 用户感知路由

    auto 模式决策逻辑（增强版）：
        ┌─────────────────────────────────────────────────────────────┐
        │ 1. 显存 < 6GB → 强制云端                                     │
        │ 2. 付费用户（paid）+ 云端可用 → 优先云端                       │
        │ 3. 免费用户（free）+ 用户偏好 cloud → 云端                    │
        │ 4. 免费用户（free）+ 云端排队过多 → 本地                       │
        │ 5. 免费用户（free）+ 自动模式 → 云端（排队<20）/ 本地（排队≥20）│
        │ 6. 云端不可用 → 本地（含降级提示）                              │
        └─────────────────────────────────────────────────────────────┘

    降级策略（降级阶梯）：
        Level 0: 原参数提交
        Level 1: 降分辨率（1280→1024, 1024→768）
        Level 2: 降步数（steps × 0.8）+ 降帧数
        Level 3: 换轻量模型变体（animatediff 代替 wan_gguf）
        Level 4: 切云端（本地持续 OOM 时）
    """

    def __init__(self):
        self._local = LocalBackend()
        self._cloud = CloudBackend()
        self._mode: InferenceMode = InferenceMode(settings.inference_mode)
        self._queue_warning_threshold = 20  # 云端排队超过此值退回本地

    @property
    def current_mode(self) -> str:
        return self._mode.value

    def set_mode(self, mode: InferenceMode | str) -> None:
        """运行时切换推理模式（前端设置页调用）。"""
        if isinstance(mode, str):
            mode = InferenceMode(mode)
        self._mode = mode
        logger.info(f"推理模式已切换为：{mode.value}")

    def get_backend(self) -> InferenceBackend:
        """
        获取当前应使用的推理后端。

        简化版（无用户上下文，仅按全局模式返回）。
        详见 decide() 获取含用户感知的完整路由决策。
        """
        if self._mode == InferenceMode.LOCAL:
            return self._local
        elif self._mode == InferenceMode.CLOUD:
            return self._cloud
        elif self._mode == InferenceMode.AUTO:
            return self._local  # auto 模式默认返回 local，智能决策由 decide() 处理
        return self._local

    def decide(
        self,
        user_context: Optional[UserContext] = None,
    ) -> tuple[InferenceBackend, dict]:
        """
        执行智能路由决策（同步版本，用于非异步场景）。

        参数：
            user_context: 用户上下文（角色、偏好、额度）

        返回：
            (选中的后端, 决策元数据)

        决策元数据结构：
            {
                "target": "local" | "cloud",
                "reason": "原因描述（前端可展示）",
                "degraded": false,  # 是否发生了降级
                "fallback_available": true,  # 是否有备选降级方案
            }

        路由决策树：
            ┌─ GLOBAL_MODE = LOCAL ─────────────────────────┐
            │  → 本地（用户明确选择本地）                      │
            ├─ GLOBAL_MODE = CLOUD ─────────────────────────┤
            │  → 云端（用户明确选择云端，不可用时降级本地）    │
            ├─ GLOBAL_MODE = AUTO ──────────────────────────┤
            │  ┌─ 付费用户(paid) ──→ 云端（优先）             │
            │  ├─ 免费用户 + 偏好 cloud ──→ 云端              │
            │  ├─ 免费用户 + 偏好 local ──→ 本地              │
            │  └─ 免费用户 + 自动  ──→ 本地（默认，避免成本）  │
            └─────────────────────────────────────────────────┘
        """
        ctx = user_context or UserContext()

        # 全局模式 LOCAL：用户明确选择本地
        if self._mode == InferenceMode.LOCAL:
            return self._local, {
                "target": "local",
                "reason": "用户选择了本地模式",
                "degraded": False,
                "fallback_available": True,
            }

        # 全局模式 CLOUD：用户明确选择云端
        if self._mode == InferenceMode.CLOUD:
            return self._cloud, {
                "target": "cloud",
                "reason": "用户选择了云端模式",
                "degraded": False,
                "fallback_available": True,
            }

        # 全局模式 AUTO：用户感知智能路由
        # 付费用户优先云端
        if ctx.role == ROLE_PAID:
            return self._cloud, {
                "target": "cloud",
                "reason": "付费用户优先使用云端加速生成",
                "degraded": False,
                "fallback_available": True,
            }

        # 免费用户根据偏好
        if ctx.preference == "cloud":
            return self._cloud, {
                "target": "cloud",
                "reason": "用户主动选择云端模式",
                "degraded": False,
                "fallback_available": True,
            }
        if ctx.preference == "local":
            return self._local, {
                "target": "local",
                "reason": "用户主动选择本地模式",
                "degraded": False,
                "fallback_available": True,
            }

        # 免费用户自动模式：保守策略，默认本地
        return self._local, {
            "target": "local",
            "reason": "自动路由：免费用户默认使用本地 ComfyUI（避免成本）",
            "degraded": False,
            "fallback_available": True,
        }

    async def should_suggest_cloud(self) -> bool:
        """
        auto 模式：是否应建议用户切换云端。

        判断条件：
            1. 本地 GPU 显存 < 阈值（6GB）
            2. 或本地 ComfyUI 不可用
        """
        try:
            health = await self._local.get_health()
            vram_total = health.get("gpu_vram_total_mb", 0)
            if vram_total > 0 and vram_total < settings.vram_threshold_mb:
                return True
            if not await self._local.is_available():
                return True
        except Exception:
            return True
        return False

    async def get_all_health(self) -> dict[str, Any]:
        """获取所有后端的健康状态（用于 /health 接口）。"""
        local_health = await self._local.get_health()
        cloud_health = await self._cloud.get_health()
        return {
            "current_mode": self._mode.value,
            "local": local_health,
            "cloud": cloud_health,
        }


# 全局单例
inference_router = InferenceRouter()
