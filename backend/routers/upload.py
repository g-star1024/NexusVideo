"""
NexusVideo Backend - 文件上传路由
============================================================
架构位置：routers/upload.py
被 local_server.py 注册为 /upload/* 路由。

功能：
    1. POST /upload/image — 图片上传，限制 10MB
    2. POST /upload/video — 视频上传，限制 200MB
    3. 文件保存到 ./uploads/{task_id}/{filename}
    4. 自动生成 task_id 或支持前端传入 task_id
    5. 支持 MIME 类型校验

文件管理策略：
    - 目录结构：./uploads/{task_id}/{filename}
    - 自动生成目录（os.makedirs）
    - 文件名保留原始扩展名，避免文件名冲突用 UUID 前缀
    - 旧文件定期清理（通过 /system/cleanup-uploads 接口）

云端模式（P2）扩展：
    - 本地模式：保存至本地 ./uploads/，通过 /static/uploads/ 直接访问
    - 云端模式：上传至 OSS/TOS，返回临时签名 URL
    - 通过 settings.inference_mode 切换策略
"""

import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, UploadFile, Query, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

router = APIRouter(prefix="/upload", tags=["文件上传"])

# ================================================================
# 上传配置
# ================================================================

# 最大图片大小：10 MB
MAX_IMAGE_SIZE = 10 * 1024 * 1024

# 最大视频大小：200 MB
MAX_VIDEO_SIZE = 200 * 1024 * 1024

# 允许的图片 MIME 类型
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/bmp",
    "image/gif",
}

# 允许的视频 MIME 类型
ALLOWED_VIDEO_TYPES = {
    "video/mp4",
    "video/mpeg",
    "video/webm",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
}

# 基础上传目录
UPLOAD_DIR = Path("./uploads")

# 确保上传目录存在
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ================================================================
# 工具函数
# ================================================================

def _safe_filename(filename: str) -> str:
    """
    生成安全的文件名，避免文件名冲突和路径穿越攻击。

    策略：{uuid8}_{原始文件名}
    例如：a1b2c3d4_photo.jpg

    Args:
        filename: 原始文件名

    Returns:
        安全后的文件名
    """
    if not filename:
        return f"{uuid.uuid4().hex[:8]}.bin"

    # 移除路径信息（防止路径穿越）
    safe_name = Path(filename).name

    # 生成 UUID 前缀避免冲突
    prefix = uuid.uuid4().hex[:8]
    return f"{prefix}_{safe_name}"


def _validate_file_size(file_size: int, max_size: int) -> None:
    """校验文件大小。"""
    if file_size > max_size:
        raise HTTPException(
            status_code=413,
            detail={
                "success": False,
                "error_code": "14001",
                "message": f"文件大小超限（{max_size / 1024 / 1024:.0f}MB），当前文件大小 {file_size / 1024 / 1024:.1f}MB",
            },
        )


def _validate_mime_type(content_type: str, allowed_types: set) -> None:
    """校验 MIME 类型。"""
    if content_type not in allowed_types:
        allowed_list = ", ".join(sorted(allowed_types))
        raise HTTPException(
            status_code=415,
            detail={
                "success": False,
                "error_code": "14002",
                "message": f"不支持的文件类型：{content_type}。支持类型：{allowed_list}",
            },
        )


async def _save_file(
    upload_file: UploadFile,
    task_id: str,
) -> tuple[str, str, int]:
    """
    保存上传的文件到本地。

    Args:
        upload_file: 上传的文件对象
        task_id: 任务 ID，用于组织目录

    Returns:
        (保存路径, 文件名, 文件大小)
    """
    # 创建任务目录
    task_dir = UPLOAD_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # 生成安全文件名
    safe_name = _safe_filename(upload_file.filename or "upload.bin")
    file_path = task_dir / safe_name

    # 读取并写入文件（大文件分块写入）
    chunk_size = 1024 * 1024  # 1MB 分块
    total_size = 0
    with open(file_path, "wb") as f:
        while chunk := await upload_file.read(chunk_size):
            f.write(chunk)
            total_size += len(chunk)

    return str(file_path), safe_name, total_size


# ================================================================
# 图片上传
# ================================================================

@router.post(
    "/image",
    summary="上传图片",
    description=(
        "接收 multipart 图片上传，保存到 ./uploads/{task_id}/{filename}。\n\n"
        "参数：\n"
        "  - file: 图片文件（必填）\n"
        "  - task_id: 关联的任务 ID（可选，不传则自动生成）\n\n"
        "限制：\n"
        "  - 最大 10MB\n"
        "  - 支持 JPEG、PNG、WebP、BMP、GIF\n\n"
        "响应：\n"
        "  {\n"
        '    "success": true,\n'
        '    "task_id": "...",\n'
        '    "filename": "...",\n'
        '    "path": "./uploads/xxx/photo.jpg",\n'
        '    "url": "http://127.0.0.1:9881/static/uploads/xxx/photo.jpg",\n'
        '    "size": 123456\n'
        "  }"
    ),
)
async def upload_image(
    file: UploadFile = File(..., description="图片文件"),
    task_id: Optional[str] = Query(
        None,
        description="关联的任务 ID，不传则自动生成",
    ),
):
    """
    上传图片文件。

    前端调用示例：
        FormData form = new FormData();
        form.append("file", fileInput.files[0]);
        form.append("task_id", taskId);
        await fetch("http://127.0.0.1:9881/upload/image?task_id=" + taskId, {
            method: "POST",
            body: form,
        });

    返回的 url 字段可直接用于前端展示预览。
    """
    # 生成或获取 task_id
    upload_task_id = task_id or uuid.uuid4().hex[:12]

    # 校验 MIME 类型
    content_type = file.content_type or ""
    _validate_mime_type(content_type, ALLOWED_IMAGE_TYPES)

    # 读取文件并校验大小
    content = await file.read()
    _validate_file_size(len(content), MAX_IMAGE_SIZE)

    # 保存到本地
    try:
        file_path, safe_name, file_size = await _save_file(
            upload_file=file,
            task_id=upload_task_id,
        )
    except Exception as e:
        logger.error(f"图片保存失败：{e}")
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error_code": "14003",
                "message": "文件保存失败，请重试",
            },
        )

    # 构建可访问 URL
    access_url = (
        f"http://127.0.0.1:9881/static/uploads/{upload_task_id}/{safe_name}"
    )

    logger.info(
        f"图片上传成功：task_id={upload_task_id}, "
        f"filename={safe_name}, size={file_size} bytes"
    )

    return {
        "success": True,
        "task_id": upload_task_id,
        "filename": safe_name,
        "path": file_path,
        "url": access_url,
        "size": file_size,
        "content_type": content_type,
    }


# ================================================================
# 视频上传
# ================================================================

@router.post(
    "/video",
    summary="上传视频",
    description=(
        "接收 multipart 视频上传，保存到 ./uploads/{task_id}/{filename}。\n\n"
        "参数：\n"
        "  - file: 视频文件（必填）\n"
        "  - task_id: 关联的任务 ID（可选，不传则自动生成）\n\n"
        "限制：\n"
        "  - 最大 200MB\n"
        "  - 支持 MP4、MPEG、WebM、MOV、AVI、MKV\n\n"
        "响应格式同图片上传。"
    ),
)
async def upload_video(
    file: UploadFile = File(..., description="视频文件"),
    task_id: Optional[str] = Query(
        None,
        description="关联的任务 ID，不传则自动生成",
    ),
):
    """
    上传视频文件。

    用于视频风格化（video2video）模式的前置步骤。
    用户上传原始视频后，将返回的 path 传给 POST /generate。
    """
    # 生成或获取 task_id
    upload_task_id = task_id or uuid.uuid4().hex[:12]

    # 校验 MIME 类型
    content_type = file.content_type or ""
    _validate_mime_type(content_type, ALLOWED_VIDEO_TYPES)

    # 读取文件并校验大小
    content = await file.read()
    _validate_file_size(len(content), MAX_VIDEO_SIZE)

    # 保存到本地
    try:
        file_path, safe_name, file_size = await _save_file(
            upload_file=file,
            task_id=upload_task_id,
        )
    except Exception as e:
        logger.error(f"视频保存失败：{e}")
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error_code": "14003",
                "message": "文件保存失败，请重试",
            },
        )

    # 构建可访问 URL
    access_url = (
        f"http://127.0.0.1:9881/static/uploads/{upload_task_id}/{safe_name}"
    )

    logger.info(
        f"视频上传成功：task_id={upload_task_id}, "
        f"filename={safe_name}, size={file_size} bytes"
    )

    return {
        "success": True,
        "task_id": upload_task_id,
        "filename": safe_name,
        "path": file_path,
        "url": access_url,
        "size": file_size,
        "content_type": content_type,
    }
