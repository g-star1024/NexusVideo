"""
NexusVideo - 一键拉取路由（/install/*）
============================================================
M1 客户端拉取链路的 HTTP 接口：
  POST /install/pull    触发拉取（target_dir 缺省取 settings.staging_dir）
  GET  /install/progress SSE 推送阶段进度（不暴露百分比/节点名/显存）
  GET  /install/verify   自检模型齐全度，返回缺失清单

不依赖 ComfyUI 运行时，故在 local_server.py 中不受 comfyui_modules_available 门控。
"""

import asyncio
import json
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings
from core.install_service import install_service

router = APIRouter(prefix="/install", tags=["安装/拉取"])


class PullRequest(BaseModel):
    target_dir: Optional[str] = None
    mirror: Optional[str] = None


@router.post("/pull")
async def pull(req: PullRequest):
    """触发一键拉取，立即返回 accepted + task_id（实际工作在后台协程进行）。"""
    if install_service is None:
        raise HTTPException(status_code=503, detail="安装服务未就绪")
    target = req.target_dir or settings.staging_dir
    task_id = uuid.uuid4().hex
    asyncio.create_task(install_service.pull(target, req.mirror, task_id))
    return {"task_id": task_id, "accepted": True}


@router.get("/progress")
async def progress():
    """SSE：推送拉取阶段文案。客户端用 EventSource 消费。"""
    if install_service is None:
        raise HTTPException(status_code=503, detail="安装服务未就绪")

    async def event_gen():
        async for ev in install_service.progress_stream():
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/verify")
async def verify(target_dir: Optional[str] = Query(None)):
    """
    自检模型齐全度。
    target_dir 缺省取 extra_model_paths.yaml 的 base_path（模型根目录）。
    返回 { missing: [...], ok: bool }。
    """
    if install_service is None:
        raise HTTPException(status_code=503, detail="安装服务未就绪")
    root = target_dir or install_service.model_base_path
    missing = await install_service.verify(root)
    return {"missing": missing, "ok": len(missing) == 0}
