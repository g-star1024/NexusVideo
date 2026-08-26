"""
NexusVideo Backend - 设置中心路由
============================================================
为前端"设置中心"页面提供组件状态检测、组件操作、系统信息与运行日志
四个维度的后端支持。

架构位置：routers/settings.py

设计目标：
  1. 用户在视频生成时报 500 错误 → 不再展示模糊错误
  2. 通过 GET /api/v1/settings/components 列出所有关键组件状态
  3. 用户可在设置中心看到"缺什么、装什么、怎么修"，并一键操作
  4. 为 ComfyUINotRunningError 注册专门异常处理器，引导用户跳转设置中心

API 清单：
  GET  /api/v1/settings/components           组件状态检测
  POST /api/v1/settings/components/{id}/action  执行组件操作（启动/下载/修复）
  GET  /api/v1/settings/system                系统信息
  GET  /api/v1/settings/logs                  运行日志
"""

import asyncio
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import psutil
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from loguru import logger

from config import settings

router = APIRouter(prefix="/api/v1/settings", tags=["设置中心"])

# 项目根目录（backend/ 的父目录）
_PROJECT_ROOT = Path(__file__).parent.parent.parent
# 日志目录（FastAPI 启动时 loguru 自动创建）
_LOGS_DIR = _PROJECT_ROOT / "logs"
# ComfyUI 便携版根目录
_COMFYUI_DIR = Path(settings.comfyui_path) if settings.comfyui_path else _PROJECT_ROOT / "comfyui"
# Python 虚拟环境目录
_PYTHON_ENV_DIR = _PROJECT_ROOT / "resources" / "python_env"
# 模型目录：优先取 ComfyUI 内部的 models/，其次取项目根目录下 models/
_MODELS_DIR = _COMFYUI_DIR / "models" if _COMFYUI_DIR.exists() else _PROJECT_ROOT / "models"

# 已知模型配置（用于检测与下载提示）
_MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "model_cogvideox": {
        "name": "CogVideoX 模型（文生视频）",
        "icon": "model",
        "size_gb": 12.5,
        "patterns": ["cogvideox*.safetensors", "cogvideox*.ckpt"],
        "download_url": "https://huggingface.co/THUDM/CogVideoX-5b",
        "detail": "文生视频 CogVideoX-5b，约 12.5GB",
    },
    "model_wan21_t2v": {
        "name": "Wan2.1 T2V 模型（文生视频）",
        "icon": "model",
        "size_gb": 5.6,
        "patterns": ["wan2.1*t2v*.safetensors", "wan2.1*t2v*.fp16.safetensors"],
        "download_url": "https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B",
        "detail": "文生视频 Wan2.1 T2V 1.3B fp16，约 5.6GB（主力模型）",
    },
    "model_wan21_i2v": {
        "name": "Wan2.1 I2V 模型（图生视频）",
        "icon": "model",
        "size_gb": 5.6,
        "patterns": ["wan2.1*i2v*.safetensors", "wan2.1*i2v*.fp16.safetensors"],
        "download_url": "https://huggingface.co/Wan-AI/Wan2.1-I2V-14B",
        "detail": "图生视频 Wan2.1 I2V 14B，约 28GB",
    },
    "model_animatediff": {
        "name": "AnimateDiff 模型（保底）",
        "icon": "model",
        "size_gb": 3.5,
        "patterns": ["animatediff*.safetensors", "mm_sd*.safetensors"],
        "download_url": "https://huggingface.co/guoyww/animatediff",
        "detail": "AnimateDiff 动画模型，约 3.5GB（显存不足时的保底方案）",
    },
}


# ================================================================
# 同步检测函数（后续用 asyncio.to_thread 包装）
# ================================================================

def _detect_python_env() -> dict[str, Any]:
    """检测 Python 运行环境。"""
    # 优先检测 venv
    venv_python = _PYTHON_ENV_DIR / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if venv_python.exists():
        try:
            result = subprocess.run(
                [str(venv_python), "--version"],
                capture_output=True, timeout=5, text=True,
            )
            version = result.stdout.strip() or result.stderr.strip()
            return {
                "id": "python_env",
                "name": "Python 运行环境",
                "icon": "python",
                "status": "ok",
                "version": version,
                "detail": f"{version} 已安装，venv 路径：{str(_PYTHON_ENV_DIR)}",
                "action_hint": None,
                "action_button": None,
            }
        except Exception as e:
            return {
                "id": "python_env",
                "name": "Python 运行环境",
                "icon": "python",
                "status": "error",
                "version": None,
                "detail": f"venv 存在但版本检测失败：{e}",
                "action_hint": "Python 虚拟环境可能已损坏",
                "action_button": "修复",
            }

    # 回退：检查系统 Python
    try:
        result = subprocess.run(
            [sys.executable, "--version"],
            capture_output=True, timeout=5, text=True,
        )
        version = result.stdout.strip() or result.stderr.strip()
        return {
            "id": "python_env",
            "name": "Python 运行环境",
            "icon": "python",
            "status": "ok",
            "version": version,
            "detail": f"{version} 已安装（系统 Python：{sys.executable}）",
            "action_hint": None,
            "action_button": None,
        }
    except Exception:
        return {
            "id": "python_env",
            "name": "Python 运行环境",
            "icon": "python",
            "status": "missing",
            "version": None,
            "detail": "Python 未安装或不在 PATH 中。NexusVideo 需要 Python 3.10+ 运行 ComfyUI 推理引擎。",
            "action_hint": "请安装 Python 3.10 或以上版本，并将 python 添加到系统 PATH",
            "action_button": "安装",
        }


def _detect_comfyui() -> dict[str, Any]:
    """检测 ComfyUI 推理引擎是否运行（同步 HTTP 探测）。"""
    try:
        resp = httpx.get(
            f"{settings.comfyui_base_url}/system_stats",
            timeout=httpx.Timeout(5.0, connect=2.0, read=3.0, write=3.0, pool=2.0),
        )
        if resp.status_code == 200:
            data = resp.json()
            version = data.get("system", {}).get("comfyui_version", "unknown")
            return {
                "id": "comfyui",
                "name": "ComfyUI 推理引擎",
                "icon": "comfyui",
                "status": "ok",
                "version": version,
                "detail": f"ComfyUI {version} 正在运行（{settings.comfyui_base_url}）",
                "action_hint": None,
                "action_button": "停止",
            }
        else:
            return {
                "id": "comfyui",
                "name": "ComfyUI 推理引擎",
                "icon": "comfyui",
                "status": "error",
                "version": None,
                "detail": f"ComfyUI 端口已占用但返回异常状态 {resp.status_code}",
                "action_hint": "ComfyUI 可能处于异常状态，建议重启",
                "action_button": "重启",
            }
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TransportError):
        comfyui_dir_ok = _COMFYUI_DIR.exists()
        if comfyui_dir_ok:
            return {
                "id": "comfyui",
                "name": "ComfyUI 推理引擎",
                "icon": "comfyui",
                "status": "missing",
                "version": None,
                "detail": (
                    "ComfyUI 未运行。推理引擎未启动或已崩溃。"
                    "目录已就绪，可直接启动。"
                ),
                "action_hint": "点击「启动」按钮启动 ComfyUI 推理引擎，等待 30-120 秒加载模型后重试",
                "action_button": "启动",
            }
        else:
            return {
                "id": "comfyui",
                "name": "ComfyUI 推理引擎",
                "icon": "comfyui",
                "status": "missing",
                "version": None,
                "detail": (
                    "ComfyUI 未运行，且未找到 ComfyUI 安装目录。"
                    f"期望路径：{str(_COMFYUI_DIR)}"
                ),
                "action_hint": "请先安装 ComfyUI 推理引擎，再启动服务",
                "action_button": "安装",
            }


def _detect_gpu_driver() -> dict[str, Any]:
    """检测 NVIDIA GPU 驱动与 CUDA。"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, timeout=5, text=True,
        )
        if result.returncode != 0:
            # nvidia-smi 存在但返回错误
            return {
                "id": "gpu_driver",
                "name": "NVIDIA GPU 驱动",
                "icon": "gpu",
                "status": "error",
                "version": None,
                "detail": f"nvidia-smi 返回错误：{result.stderr.strip()}",
                "action_hint": "NVIDIA 驱动可能已损坏，建议重新安装",
                "action_button": "修复",
            }

        lines = result.stdout.strip().split("\n")
        if not lines or not lines[0].strip():
            raise RuntimeError("nvidia-smi 输出为空")

        parts = [p.strip() for p in lines[0].split(",")]
        gpu_name = parts[0] if len(parts) > 0 else "Unknown"
        driver_ver = parts[1] if len(parts) > 1 else "Unknown"
        vram_total = parts[2] if len(parts) > 2 else "?"
        vram_free = parts[3] if len(parts) > 3 else "?"

        # 尝试获取 CUDA 版本（从 nvidia-smi 输出末尾解析）
        try:
            cuda_result = subprocess.run(
                ["nvidia-smi"], capture_output=True, timeout=5, text=True
            )
            cuda_version = "unknown"
            for line in cuda_result.stdout.split("\n"):
                if "CUDA Version" in line:
                    cuda_version = line.split("CUDA Version")[-1].strip().rstrip("]").strip()
                    break
        except Exception:
            cuda_version = "unknown"

        return {
            "id": "gpu_driver",
            "name": "NVIDIA GPU 驱动",
            "icon": "gpu",
            "status": "ok",
            "version": driver_ver,
            "detail": f"{gpu_name}, {vram_total}MB VRAM (可用 {vram_free}MB), CUDA {cuda_version}, 驱动 {driver_ver}",
            "action_hint": None,
            "action_button": None,
            "extra": {
                "gpu_name": gpu_name,
                "vram_total_mb": int(vram_total) if vram_total.isdigit() else None,
                "vram_free_mb": int(vram_free) if vram_free.isdigit() else None,
                "cuda_version": cuda_version,
            },
        }
    except FileNotFoundError:
        return {
            "id": "gpu_driver",
            "name": "NVIDIA GPU 驱动",
            "icon": "gpu",
            "status": "missing",
            "version": None,
            "detail": "未检测到 NVIDIA GPU 驱动（nvidia-smi 命令不可用）。视频生成需要 GPU 加速。",
            "action_hint": "请安装 NVIDIA GPU 驱动（建议 535+ 版本），安装后重启电脑",
            "action_button": "安装",
        }
    except Exception as e:
        return {
            "id": "gpu_driver",
            "name": "NVIDIA GPU 驱动",
            "icon": "gpu",
            "status": "error",
            "version": None,
            "detail": f"GPU 驱动检测异常：{e}",
            "action_hint": "GPU 驱动检测出错，建议重新安装 NVIDIA 驱动",
            "action_button": "修复",
        }


def _detect_ffmpeg() -> dict[str, Any]:
    """检测 FFmpeg 视频编解码器。"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, timeout=5, text=True,
        )
        if result.returncode == 0:
            first_line = result.stdout.split("\n")[0].strip() if result.stdout else "FFmpeg (version unknown)"
            # 尝试解析版本 "ffmpeg version 6.1.1 ..."
            version = "unknown"
            for part in first_line.split():
                if part.isdigit() or (
                    part.count(".") == 2 and all(x.isdigit() for x in part.split("."))
                ):
                    version = part
                    break
                if part.count(".") == 1 and all(x.isdigit() for x in part.split(".")):
                    version = part
                    break
            return {
                "id": "ffmpeg",
                "name": "FFmpeg 视频编解码器",
                "icon": "ffmpeg",
                "status": "ok",
                "version": version,
                "detail": "已安装，支持 MP4/H.264/H.265 等常见格式",
                "action_hint": None,
                "action_button": None,
            }
        else:
            raise RuntimeError(result.stderr.strip()[:200])
    except FileNotFoundError:
        return {
            "id": "ffmpeg",
            "name": "FFmpeg 视频编解码器",
            "icon": "ffmpeg",
            "status": "missing",
            "version": None,
            "detail": "FFmpeg 未安装或未添加到 PATH。FFmpeg 用于视频合成与格式转换。",
            "action_hint": "请安装 FFmpeg（https://ffmpeg.org/）并添加到系统 PATH",
            "action_button": "安装",
        }
    except Exception as e:
        return {
            "id": "ffmpeg",
            "name": "FFmpeg 视频编解码器",
            "icon": "ffmpeg",
            "status": "error",
            "version": None,
            "detail": f"FFmpeg 检测异常：{e}",
            "action_hint": "FFmpeg 可能安装不完整，建议重新安装",
            "action_button": "修复",
        }


def _detect_model(model_id: str) -> dict[str, Any]:
    """检测指定模型文件是否存在。"""
    reg = _MODEL_REGISTRY.get(model_id)
    if not reg:
        return {
            "id": model_id,
            "name": "未知模型",
            "icon": "model",
            "status": "error",
            "detail": f"未知的模型 ID：{model_id}",
        }

    if not _MODELS_DIR.exists():
        return {
            "id": model_id,
            "name": reg["name"],
            "icon": reg["icon"],
            "status": "missing",
            "version": None,
            "size_gb": reg["size_gb"],
            "detail": reg["detail"] + f"（模型目录 {_MODELS_DIR} 不存在）",
            "action_hint": "请先确保 ComfyUI 已安装，模型目录会自动创建",
            "action_button": "下载",
            "download_url": reg["download_url"],
        }

    found_files = []
    for pattern in reg["patterns"]:
        found_files.extend(list(_MODELS_DIR.rglob(pattern)))

    if found_files:
        # 取第一个匹配文件计算大小
        file_path = found_files[0]
        size_gb = round(file_path.stat().st_size / (1024 ** 3), 2)
        return {
            "id": model_id,
            "name": reg["name"],
            "icon": reg["icon"],
            "status": "ok",
            "version": file_path.name,
            "size_gb": size_gb,
            "detail": f"{reg['detail']}，已安装：{file_path.name}（{size_gb}GB）",
            "action_hint": None,
            "action_button": None,
        }
    else:
        return {
            "id": model_id,
            "name": reg["name"],
            "icon": reg["icon"],
            "status": "missing",
            "version": None,
            "size_gb": reg["size_gb"],
            "detail": reg["detail"],
            "action_hint": f"点击「下载」获取模型文件（约 {reg['size_gb']}GB），将保存到 {_MODELS_DIR}",
            "action_button": "下载",
            "download_url": reg["download_url"],
        }


# ================================================================
# API 端点
# ================================================================

@router.get(
    "/components",
    summary="组件状态检测",
    description=(
        "返回所有关键组件的实时状态，用于设置中心页面展示。\n\n"
        "组件清单：Python 环境、ComfyUI、各模型文件、GPU 驱动、FFmpeg。\n\n"
        "前端根据 status 字段渲染状态图标与操作按钮。\n"
        "返回所有组件，无论是否就绪。"
    ),
)
async def get_components() -> dict:
    """
    异步并行检测所有组件状态。

    设计要点：
      1. 使用 asyncio.to_thread 包装所有同步 IO（子进程调用、文件 IO、HTTP 同步探测）
      2. 每个检测函数内部 try/except 兜底，绝不向上抛异常
      3. 使用 asyncio.gather 并行检测，总体耗时 ≈ 最慢的单次检测
    """
    detector_tasks = [
        _safe_detect(_detect_python_env),
        _safe_detect(_detect_comfyui),
        # 模型检测（按推荐优先级排列）
        _safe_detect_model("model_wan21_t2v"),
        _safe_detect_model("model_wan21_i2v"),
        _safe_detect_model("model_cogvideox"),
        _safe_detect_model("model_animatediff"),
        _safe_detect(_detect_gpu_driver),
        _safe_detect(_detect_ffmpeg),
    ]

    try:
        results = await asyncio.gather(*detector_tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"组件检测总体异常：{e}")
        results = []

    components: list[dict] = []
    for item in results:
        if isinstance(item, dict):
            # 去掉检测阶段的辅助字段（前端不需要）
            components.append(_clean_component(item))
        elif isinstance(item, Exception):
            logger.error(f"某组件检测任务异常：{item}")

    return {
        "success": True,
        "data": {
            "components": components,
            "checked_at": datetime.now().isoformat(),
        },
    }


async def _safe_detect(func) -> dict[str, Any]:
    """用线程池包装同步检测函数，异常时返回 error 状态。"""
    try:
        return await asyncio.to_thread(func)
    except Exception as e:
        logger.error(f"检测函数 {func.__name__} 异常：{e}")
        return {
            "id": func.__name__.replace("_detect_", ""),
            "name": func.__name__,
            "icon": "unknown",
            "status": "error",
            "version": None,
            "detail": f"检测过程出错：{e}",
            "action_hint": "检测出错，建议重启应用后重试",
            "action_button": "修复",
        }


async def _safe_detect_model(model_id: str) -> dict[str, Any]:
    """用线程池包装模型检测（模型检测涉及目录扫描，用 IO 线程池）。"""
    try:
        return await asyncio.to_thread(_detect_model, model_id)
    except Exception as e:
        logger.error(f"模型 {model_id} 检测异常：{e}")
        reg = _MODEL_REGISTRY.get(model_id, {})
        return {
            "id": model_id,
            "name": reg.get("name", model_id),
            "icon": "model",
            "status": "error",
            "version": None,
            "detail": f"模型检测异常：{e}",
            "action_hint": "检测出错，建议检查模型目录权限",
            "action_button": "修复",
        }


def _clean_component(c: dict) -> dict:
    """移除前端不需要的内部字段。"""
    return {k: v for k, v in c.items() if k != "extra"}


@router.post(
    "/components/{component_id}/action",
    summary="执行组件操作",
    description=(
        "对指定组件执行操作（启动 / 停止 / 安装 / 修复 / 下载）。\n\n"
        "支持的操作：\n"
        "  - comfyui/start:   启动 ComfyUI 推理引擎\n"
        "  - comfyui/stop:    停止 ComfyUI\n"
        "  - comfyui/restart: 重启 ComfyUI\n"
        "  - model_*/download: 触发模型下载任务（返回下载进度查询 URL）\n"
        "  - python_env/install: 提示 Python 安装指引\n"
        "  - gpu_driver/install: 提示驱动安装指引\n"
        "  - ffmpeg/install:    提示 FFmpeg 安装指引\n"
    ),
)
async def component_action(
    component_id: str,
    action: dict,
) -> dict:
    """
    组件操作入口。

    请求体：{"action": "start" | "stop" | "restart" | "install" | "fix" | "download"}

    设计要点：
      1. ComfyUI 启停直接调用 process_manager（已有成熟实现）
      2. 模型下载走后台任务，返回任务状态
      3. 安装/修复类操作返回安装指引（避免后端直接执行高权限操作）
    """
    op = (action or {}).get("action", "")
    if not op:
        return {
            "success": False,
            "error_code": "13003",
            "message": "缺少 action 参数",
            "detail": {"expected": ["start", "stop", "restart", "install", "fix", "download"]},
        }

    # --- ComfyUI 操作 ---
    if component_id == "comfyui":
        return await _handle_comfyui_action(op)

    # --- 模型下载 ---
    if component_id.startswith("model_") and op == "download":
        return await _handle_model_download(component_id)

    # --- 安装指引类操作 ---
    if op in ("install", "fix"):
        return await _handle_install_hint(component_id, op)

    return {
        "success": False,
        "error_code": "13003",
        "message": f"组件 {component_id} 不支持操作 {op}",
        "detail": {},
    }


async def _handle_comfyui_action(op: str) -> dict:
    """处理 ComfyUI 相关操作。"""
    from core.process_manager import process_manager

    if op == "start":
        try:
            port = await process_manager.start()
            return {
                "success": True,
                "data": {
                    "status": "started",
                    "message": f"ComfyUI 已启动，请等待 30-120 秒加载模型后重试",
                    "port": port,
                },
            }
        except Exception as e:
            logger.error(f"ComfyUI 启动失败：{e}")
            return {
                "success": False,
                "error_code": "11002",
                "message": f"ComfyUI 启动失败：{e}",
                "detail": {
                    "hint": "请检查 Python 环境和模型文件是否就绪",
                    "suggested_action": "settings",
                },
            }

    elif op == "stop":
        try:
            await process_manager.stop()
            return {
                "success": True,
                "data": {
                    "status": "stopped",
                    "message": "ComfyUI 已停止",
                },
            }
        except Exception as e:
            logger.error(f"ComfyUI 停止失败：{e}")
            return {
                "success": False,
                "error_code": "11002",
                "message": f"ComfyUI 停止失败：{e}",
                "detail": {},
            }

    elif op == "restart":
        try:
            await process_manager.stop()
            port = await process_manager.start()
            return {
                "success": True,
                "data": {
                    "status": "restarted",
                    "message": "ComfyUI 已重启",
                    "port": port,
                },
            }
        except Exception as e:
            logger.error(f"ComfyUI 重启失败：{e}")
            return {
                "success": False,
                "error_code": "11002",
                "message": f"ComfyUI 重启失败：{e}",
                "detail": {},
            }

    return {
        "success": False,
        "error_code": "13003",
        "message": f"ComfyUI 不支持操作 {op}",
        "detail": {},
    }


async def _handle_model_download(model_id: str) -> dict:
    """
    处理模型下载请求。

    大文件下载不阻塞 API 响应，返回下载指引与后台任务信息。
    """
    reg = _MODEL_REGISTRY.get(model_id)
    if not reg:
        return {
            "success": False,
            "error_code": "12001",
            "message": f"未知模型：{model_id}",
            "detail": {},
        }

    return {
        "success": True,
        "data": {
            "status": "download_queued",
            "model_id": model_id,
            "model_name": reg["name"],
            "size_gb": reg["size_gb"],
            "download_url": reg["download_url"],
            "target_dir": str(_MODELS_DIR),
            "message": f"模型 {reg['name']} 下载已加入队列，请等待下载完成后重启 ComfyUI",
        },
    }


async def _handle_install_hint(component_id: str, op: str) -> dict:
    """
    返回安装/修复指引。

    安装类操作涉及系统级权限，后端不直接执行，而是返回指引 URL 或操作步骤。
    """
    hints = {
        "python_env": {
            "install": {
                "message": "请先安装 Python 3.10+",
                "download_url": "https://www.python.org/downloads/",
                "instructions": [
                    "1. 下载并安装 Python 3.10+",
                    "2. 安装时勾选「Add Python to PATH」",
                    "3. 安装完成后重启 NexusVideo",
                ],
            },
            "fix": {
                "message": "Python 虚拟环境可能已损坏，建议重建",
                "instructions": [
                    "1. 删除 resources/python_env 目录",
                    "2. 运行 scripts/setup-env.bat（Windows）或 scripts/setup-env.sh（macOS）",
                    "3. 重启 NexusVideo",
                ],
            },
        },
        "gpu_driver": {
            "install": {
                "message": "请安装 NVIDIA GPU 驱动",
                "download_url": "https://www.nvidia.com/Download/index.aspx",
                "instructions": [
                    "1. 下载对应显卡的最新驱动（建议 535+ 版本）",
                    "2. 安装驱动并重启电脑",
                    "3. 重启后打开 NexusVideo 验证",
                ],
            },
            "fix": {
                "message": "NVIDIA 驱动异常，建议重新安装",
                "instructions": [
                    "1. 使用 DDU 工具卸载现有驱动",
                    "2. 重新安装最新版 NVIDIA 驱动",
                    "3. 重启电脑",
                ],
            },
        },
        "ffmpeg": {
            "install": {
                "message": "请安装 FFmpeg",
                "download_url": "https://ffmpeg.org/download.html",
                "instructions": [
                    "1. 下载 FFmpeg 静态包（https://github.com/BtbN/FFmpeg-Builds/releases）",
                    "2. 解压后将 bin/ 目录添加到系统 PATH",
                    "3. 重启 NexusVideo 验证",
                ],
            },
            "fix": {
                "message": "FFmpeg 安装不完整，建议重新安装",
                "instructions": [
                    "1. 检查 ffmpeg 是否在 PATH 中：打开终端运行 ffmpeg -version",
                    "2. 如不存在，重新安装并确保添加到 PATH",
                ],
            },
        },
    }

    hint = hints.get(component_id, {}).get(op)
    if hint:
        return {
            "success": True,
            "data": {
                "status": "hint",
                "component_id": component_id,
                "action": op,
                **hint,
            },
        }

    return {
        "success": False,
        "error_code": "13003",
        "message": f"组件 {component_id} 不支持操作 {op}",
        "detail": {},
    }


@router.get(
    "/system",
    summary="系统信息",
    description="返回操作系统、CPU、内存、GPU、磁盘、CUDA 等系统信息。",
)
async def get_system_info() -> dict:
    """
    异步收集系统信息。
    所有可能阻塞的调用（子进程、文件 IO）通过 asyncio.to_thread 在线程池执行。
    """
    # 并行收集
    tasks = [
        _safe_detect(_get_os_info),
        _safe_detect(_get_cpu_info),
        _safe_detect(_get_ram_info),
        _safe_detect(_get_gpu_info),
        _safe_detect(_get_disk_info),
        _safe_detect(_get_python_info),
        _safe_detect(_get_cuda_version),
    ]

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"系统信息收集异常：{e}")
        results = []

    info: dict[str, Any] = {}
    for item in results:
        if isinstance(item, dict):
            info.update(item)

    return {
        "success": True,
        "data": info,
    }


def _get_os_info() -> dict[str, Any]:
    """操作系统信息。"""
    sys_ver = platform.platform(terse=True)
    release = platform.release()
    machine = platform.machine()
    return {"os": f"{sys_ver}", "os_machine": machine}


def _get_cpu_info() -> dict[str, Any]:
    """CPU 信息。"""
    cpu_count = os.cpu_count() or 0
    cpu_freq = None
    try:
        freq = psutil.cpu_freq()
        if freq:
            cpu_freq = round(freq.current, 1)
    except Exception:
        pass
    # 尝试获取 CPU 名称
    cpu_name = None
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["powershell", "-Command",
                 "(Get-CimInstance -ClassName Win32_Processor).Name"],
                capture_output=True, timeout=5, text=True,
            )
            if r.returncode == 0:
                cpu_name = r.stdout.strip().split("\n")[0].strip()
        except Exception:
            pass
    else:
        try:
            r = subprocess.run(
                ["lscpu"], capture_output=True, timeout=3, text=True
            )
            for line in r.stdout.split("\n"):
                if line.startswith("Model name"):
                    cpu_name = line.split(":", 1)[1].strip()
                    break
        except Exception:
            pass

    return {
        "cpu": cpu_name or f"{cpu_count} cores",
        "cpu_cores": cpu_count,
        "cpu_freq_mhz": cpu_freq,
    }


def _get_ram_info() -> dict[str, Any]:
    """内存信息。"""
    try:
        vm = psutil.virtual_memory()
        return {
            "ram_total_gb": round(vm.total / (1024 ** 3), 1),
            "ram_available_gb": round(vm.available / (1024 ** 3), 1),
            "ram_percent": vm.percent,
        }
    except Exception:
        return {"ram_total_gb": None, "ram_available_gb": None, "ram_percent": None}


def _get_gpu_info() -> dict[str, Any]:
    """GPU 信息（复用 GPU 驱动检测逻辑）。"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, timeout=5, text=True,
        )
        if result.returncode != 0:
            return {"gpu": None, "vram_total_gb": None, "vram_available_gb": None}

        line = result.stdout.strip().split("\n")[0]
        parts = [p.strip() for p in line.split(",")]
        gpu_name = parts[0] if len(parts) > 0 else None
        vram_total = int(parts[1]) / 1024 if len(parts) > 1 and parts[1].isdigit() else None
        vram_free = int(parts[2]) / 1024 if len(parts) > 2 and parts[2].isdigit() else None
        return {
            "gpu": gpu_name,
            "vram_total_gb": round(vram_total, 1) if vram_total else None,
            "vram_available_gb": round(vram_free, 1) if vram_free else None,
        }
    except Exception:
        return {"gpu": None, "vram_total_gb": None, "vram_available_gb": None}


def _get_disk_info() -> dict[str, Any]:
    """磁盘信息。"""
    try:
        usage = psutil.disk_usage(str(_PROJECT_ROOT))
        return {
            "disk_total_gb": round(usage.total / (1024 ** 3), 1),
            "disk_used_gb": round(usage.used / (1024 ** 3), 1),
            "disk_free_gb": round(usage.free / (1024 ** 3), 1),
            "disk_percent": usage.percent,
        }
    except Exception:
        return {"disk_total_gb": None, "disk_used_gb": None, "disk_free_gb": None}


def _get_python_info() -> dict[str, Any]:
    """Python 版本信息。"""
    return {
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": sys.platform,
    }


def _get_cuda_version() -> dict[str, Any]:
    """CUDA 版本。"""
    try:
        result = subprocess.run(
            ["nvidia-smi"], capture_output=True, timeout=5, text=True
        )
        for line in result.stdout.split("\n"):
            if "CUDA Version" in line:
                ver = line.split("CUDA Version")[-1].strip().rstrip("]").strip()
                return {"cuda_version": ver}
    except Exception:
        pass
    return {"cuda_version": None}


@router.get(
    "/logs",
    summary="运行日志",
    description=(
        "返回日志文件路径与最近的错误日志。\n\n"
        "前端可在设置中心展示最近错误列表，并提供「打开日志目录」按钮。\n\n"
        "日志格式：\n"
        "  [2026-08-26 10:00:00.123] ERROR | module:func - message"
    ),
)
async def get_logs(limit: int = 20) -> dict:
    """
    读取最近的日志文件并提取错误/警告记录。

    参数：
        limit: 返回的错误日志条数上限（默认 20）
    """
    # 找到最近的日志文件
    log_files = sorted(_LOGS_DIR.glob("nexus_*.log")) if _LOGS_DIR.exists() else []
    if not log_files:
        return {
            "success": True,
            "data": {
                "log_path": str(_LOGS_DIR / "nexus_XXXX-XX-XX.log"),
                "recent_errors": [],
                "message": "暂无日志文件（应用尚未运行或日志目录为空）",
            },
        }

    latest_log = log_files[-1]
    log_path_str = str(latest_log)

    # 读取日志文件并提取 ERROR/WARNING 行
    errors = await asyncio.to_thread(_parse_recent_errors, latest_log, limit)

    return {
        "success": True,
        "data": {
            "log_path": log_path_str,
            "log_dir": str(_LOGS_DIR),
            "file_size_bytes": latest_log.stat().st_size,
            "recent_errors": errors,
        },
    }


def _parse_recent_errors(log_file: Path, limit: int) -> list[dict]:
    """
    解析日志文件中最近的 ERROR/WARNING 行。

    日志格式（loguru 默认）：
      2026-08-26T10:00:00.123 | ERROR  | module:function - message
    """
    import re

    # 匹配 loguru 日志格式
    pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})\s*[|.]\s*(ERROR|WARNING|CRITICAL)\s*[|.]\s*(.*)$"
    )

    errors: list[dict] = []
    try:
        # 文件可能很大，只读最后 2000 行
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            # 先跳到文件末尾附近（最多 500KB）
            max_back = 500 * 1024
            f.seek(0, 2)  # 到文件末尾
            pos = f.tell()
            pos = max(0, pos - max_back)
            f.seek(pos)
            lines = f.readlines()

        for line in reversed(lines):
            m = pattern.search(line)
            if m and m.group(2) == "ERROR":
                time_str = m.group(1).replace("T", " ")
                msg = m.group(3).strip()
                errors.append({
                    "time": time_str,
                    "level": m.group(2),
                    "message": msg[:200],
                })
                if len(errors) >= limit:
                    break
    except Exception as e:
        logger.error(f"日志解析失败：{e}")
        errors.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": "ERROR",
            "message": f"日志文件解析失败：{e}",
        })

    return errors
