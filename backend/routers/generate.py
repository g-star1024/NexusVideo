"""
NexusVideo Backend - 生成路由 v2
============================================================
/generate 接口：前端"一键生成"的入口点。

v2 更新：
  1. 三大模式完整支持（txt2video / img2video / video2video）
  2. 运动强度滑块参数透传
  3. 视频风格化风格预设参数透传
  4. 输入文件路径透传（图/视频）
  5. 显存档位模型变体选择
"""

import uuid
from fastapi import APIRouter, status

from loguru import logger

from core.task_manager import task_manager
from core.workflow_translator import motion_strength_to_denoising
from models.schemas import (
    GenerateRequest,
    GenerateResponse,
    TaskStatus,
    GenerationMode,
)

router = APIRouter(tags=["生成"])


@router.post(
    "/generate",
    response_model=GenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="提交视频生成任务",
    description=(
        "接收前端简化参数（提示词 + 模式选择），后端自动翻译为 "
        "ComfyUI 工作流 JSON 并提交推理引擎。\n\n"
        "三大模式：\n"
        "  - txt2video: 文生视频，仅需 prompt\n"
        "  - img2video: 图生视频，需要 input_image + motion_strength\n"
        "  - video2video: 视频风格化，需要 video_path + style\n\n"
        "返回 task_id，前端通过 GET /task/{task_id} 轮询任务状态。"
    ),
)
async def generate(request: GenerateRequest) -> GenerateResponse:
    """
    一键生成视频（白皮书 4.2 节核心接口）。

    请求示例：
        # 文生视频
        POST /generate
        {
            "prompt": "赛博朋克风格的猫在雨中走，电影级光影",
            "mode": "txt2video"
        }

        # 图生视频
        POST /generate
        {
            "prompt": "微风拂过，树叶轻轻摇曳",
            "mode": "img2video",
            "input_image": "C:/Users/input/photo.jpg",
            "motion_strength": 5
        }

        # 视频风格化
        POST /generate
        {
            "prompt": "一条宁静的小河穿过竹林",
            "mode": "video2video",
            "video_path": "C:/Users/input/source.mp4",
            "style": "oil"
        }

    响应示例：
        202 Accepted
        {
            "task_id": "a1b2c3d4-...",
            "seed": 738291,
            "mode": "txt2video",
            "status": "queued",
            "message": "任务已提交，正在排队"
        }
    """
    logger.info(
        f"收到生成请求：mode={request.mode.value}, "
        f"prompt='{request.prompt[:80]}...'"
    )

    # 模式特定参数校验
    if request.mode == GenerationMode.IMG2VIDEO and not request.input_image:
        logger.warning("图生视频模式缺少 input_image，将使用默认占位图")

    if request.mode == GenerationMode.VIDEO2VIDEO and not request.video_path:
        logger.warning("视频风格化模式缺少 video_path")

    # 记录运动强度映射结果（便于调试）
    if request.motion_strength is not None:
        denoise = motion_strength_to_denoising(request.motion_strength)
        logger.info(
            f"运动强度映射：motion_strength={request.motion_strength} "
            f"→ denoising_strength={denoise}"
        )

    # 如果用户同时传了 motion_strength 和 denoising_strength，
    # denoising_strength 优先级更高
    if request.motion_strength is not None and request.denoising_strength is not None:
        logger.info(
            f"同时传入了 motion_strength({request.motion_strength}) 和 "
            f"denoising_strength({request.denoising_strength})，"
            f"使用 denoising_strength"
        )

    # 提交任务
    task_id, resolved_seed = await task_manager.submit_and_track(request)

    return GenerateResponse(
        task_id=task_id,
        seed=resolved_seed,
        mode=request.mode.value,
        status=TaskStatus.QUEUED,
        message="任务已提交，正在排队",
    )