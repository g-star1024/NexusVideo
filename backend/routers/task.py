"""
NexusVideo Backend - 任务状态路由
============================================================
/task/{task_id} 接口：查询任务执行状态与结果。

架构位置：routers/task.py
前端通过此接口轮询任务进度（P0 阶段轮询，P1 阶段改用 WebSocket）。
"""

from fastapi import APIRouter, status

from loguru import logger

from core.task_manager import task_manager, get_stage_message
from models.schemas import TaskStatusResponse, TaskStatus
from exceptions import TaskNotFoundError

router = APIRouter(tags=["任务"])


@router.get(
    "/task/{task_id}",
    response_model=TaskStatusResponse,
    summary="查询任务状态",
    description=(
        "根据 task_id 查询视频生成任务的当前状态、进度和结果。\n\n"
        "前端建议每 1-2 秒轮询一次，任务完成后停止轮询。"
    ),
)
async def get_task_status(task_id: str) -> TaskStatusResponse:
    """
    查询任务状态。

    响应示例（运行中）：
        {
            "task_id": "a1b2c3d4-...",
            "status": "running",
            "progress": 45.0,
            "stage_message": "画面已成型，正在精雕细琢...",
            "output_url": null
        }

    响应示例（完成）：
        {
            "task_id": "a1b2c3d4-...",
            "status": "success",
            "progress": 100.0,
            "stage_message": "生成完成！",
            "output_url": "http://127.0.0.1:8188/view?filename=...",
            "output_filename": "NexusVideo_00001_.mp4"
        }
    """
    record = task_manager.get_task_status(task_id)

    if record is None:
        raise TaskNotFoundError(task_id)

    elapsed = None
    if record.completed_at:
        elapsed = round(record.completed_at - record.created_at, 1)

    return TaskStatusResponse(
        task_id=record.task_id,
        status=record.status,
        progress=record.progress,
        stage_message=record.stage_message,
        output_url=record.output_url,
        output_filename=record.output_filename,
        error=record.error,
        error_code=record.error_code,
        created_at=str(record.created_at),
        completed_at=str(record.completed_at) if record.completed_at else None,
        elapsed_seconds=elapsed,
    )


@router.post(
    "/task/{task_id}/cancel",
    summary="取消任务",
    description="取消正在执行或排队的任务。",
)
async def cancel_task(task_id: str) -> dict:
    """取消指定任务。"""
    from core.comfyui_client import comfyui_client

    record = task_manager.get_task_status(task_id)
    if record is None:
        raise TaskNotFoundError(task_id)

    if record.status in (TaskStatus.RUNNING, TaskStatus.QUEUED):
        try:
            await comfyui_client.interrupt()
        except Exception as e:
            logger.warning(f"中断 ComfyUI 任务失败：{e}")

        record.status = TaskStatus.FAILED
        record.error = "用户取消"
        record.completed_at = __import__("time").time()

    return {"task_id": task_id, "status": record.status.value}
