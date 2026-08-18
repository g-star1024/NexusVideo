"""
NexusVideo Backend - 系统路由
============================================================
/health, /comfyui/* 等系统管理接口。

架构位置：routers/system.py
负责健康检测、ComfyUI 进程管理、推理模式切换等。
"""

from fastapi import APIRouter

from loguru import logger

from config import settings
from core.process_manager import process_manager
from core.inference_router import inference_router
from core.task_manager import task_manager
from core.comfyui_client import comfyui_client
from models.schemas import (
    HealthResponse,
    ComfyUIProcessStatus,
    ErrorResponse,
)
from models.schemas import InferenceMode

router = APIRouter(tags=["系统"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="健康检测",
    description="检测 FastAPI + ComfyUI + GPU 的整体健康状态。",
)
async def health_check() -> HealthResponse:
    """
    健康检测接口（Tauri 前端启动时调用，判断是否可以开始生成）。

    返回整体状态：
        - healthy:   FastAPI + ComfyUI + GPU 全部正常
        - degraded:  ComfyUI 可用但 GPU 不可用（CPU 模式，会很慢）
        - unhealthy: ComfyUI 不可用
    """
    comfyui_ok = False
    gpu_available = False
    gpu_name = None
    gpu_vram_total = None
    gpu_vram_used = None

    try:
        stats = await comfyui_client.health_check()
        comfyui_ok = True

        devices = stats.get("devices", [])
        if devices:
            dev = devices[0]
            gpu_name = dev.get("name")
            gpu_available = dev.get("type") == "cuda"
            vram_total = dev.get("vram_total", 0)
            vram_free = dev.get("vram_free", 0)
            gpu_vram_total = vram_total // (1024 * 1024) if vram_total else None
            gpu_vram_used = (vram_total - vram_free) // (1024 * 1024) if vram_total else None
    except Exception as e:
        logger.warning(f"ComfyUI 健康检测失败：{e}")

    # 综合状态判定
    if comfyui_ok and gpu_available:
        overall = "healthy"
    elif comfyui_ok:
        overall = "degraded"
    else:
        overall = "unhealthy"

    return HealthResponse(
        status=overall,
        comfyui=comfyui_ok,
        comfyui_url=settings.comfyui_base_url,
        gpu_available=gpu_available,
        gpu_name=gpu_name,
        gpu_vram_total_mb=gpu_vram_total,
        gpu_vram_used_mb=gpu_vram_used,
        active_tasks=task_manager.active_count,
        inference_mode=inference_router.current_mode,
    )


@router.get(
    "/comfyui/status",
    response_model=ComfyUIProcessStatus,
    summary="查询 ComfyUI 进程状态",
)
async def comfyui_status() -> ComfyUIProcessStatus:
    """查询 ComfyUI 子进程的运行状态、PID、资源占用。"""
    status_dict = await process_manager.get_status()
    return ComfyUIProcessStatus(**status_dict)


@router.post(
    "/comfyui/start",
    summary="启动 ComfyUI",
    description="手动启动 ComfyUI 推理引擎（通常由 FastAPI 自动启动）。",
)
async def comfyui_start() -> dict:
    """手动启动 ComfyUI 进程。"""
    port = await process_manager.start()
    return {"status": "started", "port": port}


@router.post(
    "/comfyui/stop",
    summary="停止 ComfyUI",
)
async def comfyui_stop() -> dict:
    """手动停止 ComfyUI 进程。"""
    await process_manager.stop()
    return {"status": "stopped"}


@router.post(
    "/comfyui/restart",
    summary="重启 ComfyUI",
)
async def comfyui_restart() -> dict:
    """重启 ComfyUI 进程（修改工作流/节点后可能需要）。"""
    await process_manager.stop()
    port = await process_manager.start()
    return {"status": "restarted", "port": port}


@router.get(
    "/inference/mode",
    summary="查询当前推理模式",
    description="返回当前推理模式（local/cloud/auto）及各后端可用性。",
)
async def get_inference_mode() -> dict:
    """查询推理模式与后端健康状态。"""
    return await inference_router.get_all_health()


@router.post(
    "/inference/mode",
    summary="切换推理模式",
    description="切换本地/云端推理模式（P2 阶段完整启用）。",
)
async def set_inference_mode(mode: str) -> dict:
    """
    切换推理模式。

    请求体：{"mode": "local" | "cloud" | "auto"}
    """
    valid_modes = [m.value for m in InferenceMode]
    if mode not in valid_modes:
        from exceptions import InvalidInputError
        raise InvalidInputError(f"无效的推理模式：{mode}，可选：{valid_modes}")

    inference_router.set_mode(mode)
    return {"status": "ok", "mode": mode}


@router.get(
    "/inference/suggest-cloud",
    summary="是否建议切换云端",
    description="auto 模式下检测本地显存是否不足，返回是否建议用户切换云端。",
)
async def suggest_cloud() -> dict:
    """检测是否应建议用户切换到云端模式。"""
    should = await inference_router.should_suggest_cloud()
    return {
        "suggest_cloud": should,
        "threshold_mb": settings.vram_threshold_mb,
        "message": (
            "检测到您的显卡性能有限，是否开启云端极速模式？"
            if should else "本地 GPU 性能充足"
        ),
    }
