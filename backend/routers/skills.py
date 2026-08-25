"""
NexusVideo Backend - 技能中心路由（Skill Registry API）
============================================================
GET  /skills                         列出全部技能摘要
GET  /skills/{skill_id}              获取完整 manifest
GET  /skills/{skill_id}/readiness    依赖就绪预检（可选）
POST /skills/{skill_id}/generate     提交技能生成任务（复用现有派发链）

设计：generate 端点复用 task_manager.submit_prepared_workflow，
因此技能任务的进度查询完全复用现有 GET /task/{task_id} 与
WS /progress/ws（前端无需改 TS）。ComfyUI 未启动时复用现有
503 COMFYUI_UNAVAILABLE 错误（上一轮修复路径）。
"""

from fastapi import APIRouter, status

from loguru import logger

from core.skill_registry import skill_registry
from core.task_manager import task_manager
from models.schemas import (
    SkillMeta,
    SkillManifest,
    SkillGenerateRequest,
    SkillGenerateResponse,
    SkillReadinessResponse,
)
from exceptions import SkillNotFoundError

router = APIRouter(prefix="/skills", tags=["技能中心"])


@router.get(
    "",
    response_model=list[SkillMeta],
    summary="列出全部内置技能",
    description="返回已注册技能的摘要列表，供前端 Gallery 渲染卡片。",
)
async def list_skills() -> list[SkillMeta]:
    """列出全部技能（GET /skills）。"""
    return skill_registry.list_skills()


@router.get(
    "/{skill_id}",
    response_model=SkillManifest,
    summary="获取技能完整 manifest",
    responses={404: {"description": "技能不存在"}},
)
async def get_skill(skill_id: str) -> SkillManifest:
    """获取单个技能完整 manifest（GET /skills/{id}）。"""
    manifest = skill_registry.get_skill(skill_id)
    if manifest is None:
        raise SkillNotFoundError(skill_id)
    return manifest


@router.get(
    "/{skill_id}/readiness",
    response_model=SkillReadinessResponse,
    summary="技能依赖就绪预检",
    responses={404: {"description": "技能不存在"}},
)
async def skill_readiness(skill_id: str) -> SkillReadinessResponse:
    """预检技能依赖（模型/自定义节点/工作流完整性）。"""
    readiness = skill_registry.check_readiness(skill_id)
    return SkillReadinessResponse(**readiness)


@router.post(
    "/{skill_id}/generate",
    response_model=SkillGenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="提交技能生成任务",
    description=(
        "复用现有任务派发链（task_manager.submit_prepared_workflow）。\n\n"
        "成功返回 202 + task_id；前端用现有 GET /task/{task_id} 与 "
        "WS /progress/ws 查询进度。\n\n"
        "ComfyUI 未启动 → 复用现有 503 COMFYUI_UNAVAILABLE 错误；"
        "技能不存在 → 404 SKILL_NOT_FOUND。"
    ),
    responses={
        404: {"description": "技能不存在"},
        503: {"description": "ComfyUI 未运行 / 技能依赖缺失"},
    },
)
async def skill_generate(
    skill_id: str, request: SkillGenerateRequest
) -> SkillGenerateResponse:
    """提交技能生成任务（POST /skills/{id}/generate）。"""
    logger.info(
        f"收到技能生成请求：skill={skill_id}, prompt='{request.prompt[:80]}...'"
    )

    # 1. 校验技能存在（不存在 → 404 SKILL_NOT_FOUND，由全局异常处理器转换）
    manifest = skill_registry.get_skill(skill_id)
    if manifest is None:
        raise SkillNotFoundError(skill_id)

    # 2. 合并参数：用户 prompt 进入 params（build_workflow 会解析 seed/负向提示词）
    params: dict = dict(request.params or {})
    params["prompt"] = request.prompt
    if request.seed is not None:
        params["seed"] = request.seed

    # 3. 构建工作流（复用占位符机制，零重复实现）
    workflow, resolved_seed = skill_registry.build_workflow(skill_id, params)

    # 4. 提交（复用队列/异常/降级全链路）
    task_id, _ = await task_manager.submit_prepared_workflow(
        workflow=workflow,
        skill_id=skill_id,
        params=params,
        seed=resolved_seed,
    )

    return SkillGenerateResponse(
        task_id=task_id,
        seed=resolved_seed,
        status="queued",
        skill_id=skill_id,
    )
