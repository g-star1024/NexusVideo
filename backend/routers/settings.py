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
  POST /api/v1/settings/components/{id}/action  执行组件操作（启动/下载/修复/安装）
  GET  /api/v1/settings/components/comfyui/install-status  ComfyUI 安装进度轮询
  GET  /api/v1/settings/system                系统信息
  GET  /api/v1/settings/logs                  运行日志
"""

import asyncio
import os
import platform
import shutil
import subprocess
import sys
import time
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
# ComfyUI 一键安装：常量与全局状态
# ================================================================
# 官方仓库（可用 COMFYUI_GIT_MIRROR 环境变量覆盖为镜像/代理地址）
_COMFYUI_REPO_DEFAULT = "https://github.com/comfyanonymous/ComfyUI.git"

# PyTorch CUDA 轮子索引。cu124 覆盖 Turing(sm_75) ~ Blackwell，
# 兼容 GTX 16xx / RTX 20xx-40xx；可用 TORCH_INDEX_URL 覆盖为国内镜像。
_TORCH_CUDA_INDEX_DEFAULT = "https://download.pytorch.org/whl/cu124"

# 各阶段超时（秒）。torch 轮子约 2.5GB，慢网络下需要较长时间。
_TIMEOUT_GIT_CLONE = 300
_TIMEOUT_TORCH = 1800
_TIMEOUT_REQUIREMENTS = 900
_TIMEOUT_VERIFY = 120

# 各阶段预估耗时（秒），仅用于进度条平滑推进（非硬性约束）
_ETA_GIT_CLONE = 60.0
_ETA_TORCH = 600.0
_ETA_REQUIREMENTS = 180.0

# 阶段 → (进度区间下界, 上界, 中文标签)
_INSTALL_STAGES: dict[str, tuple[int, int, str]] = {
    "precheck": (0, 5, "环境检查"),
    "clone": (5, 40, "下载 ComfyUI 源码"),
    "torch": (40, 80, "安装 PyTorch（CUDA 加速）"),
    "requirements": (80, 96, "安装依赖库"),
    "verify": (96, 100, "校验安装结果"),
    "done": (100, 100, "安装完成"),
    "failed": (0, 0, "安装失败"),
}

# 安装失败错误码。
# 注意：exceptions.py 中 11003 当前已被 COMFYUI_TIMEOUT 占用，
# 此处沿用团队约定的 11003 以保持前后端契约一致，并通过
# detail.error_kind = "comfyui_install_failed" 做二次区分。
# 后续如在 exceptions.py 补充 COMFYUI_INSTALL_FAILED = "11008"，
# 只需改动此处一行常量即可完成迁移。
_ERR_COMFYUI_INSTALL_FAILED = "11003"

# 安装日志尾部保留行数（供前端展示"正在做什么"与排障）
_INSTALL_LOG_TAIL_MAX = 60

# 安装任务全局状态。单进程单事件循环内读写，无需加锁。
#
# 状态机字段与前端契约（client/src/pages/SettingsView.vue）严格对齐：
#   - status 仅取 idle | installing | done | error
#     前端只在 done / error 时停止轮询，其余值都会继续轮询，
#     因此绝不能返回 success / failed 之类的别名，否则进度条永不结束。
#   - stage 是**中文文案**，前端直接渲染给用户看；
#     机器可读的阶段枚举放在 stage_key。
_comfyui_install_state: dict[str, Any] = {
    "status": "idle",              # idle | installing | done | error
    "progress": 0,                 # 0-100
    "stage": "",                   # 中文阶段文案（前端直接展示）
    "stage_key": "",               # precheck | clone | torch | requirements | verify | done | failed
    "stage_label": "",             # = stage，保留别名便于其他调用方使用
    "message": "",                 # 当前动作的一行描述（失败时 = 原因 + 处置建议）
    "raw_message": None,           # 失败时的原始技术原因（不含建议），便于排障
    "hint": None,                  # 失败时的处置建议
    "error_code": None,
    "started_at": None,
    "finished_at": None,
    "path": None,                  # 安装目标目录
    "python_executable": None,     # 实际用于安装依赖的解释器
    "python_warning": None,        # 解释器版本风险提示
    "cuda_mode": None,             # cuda | cpu
    "torch_version": None,
    "cuda_available": None,
    "mirrors": None,               # 实际生效的镜像配置
    "log_tail": [],
}

# 正在执行的安装任务句柄（用于幂等：安装中重复点击不会起第二个任务）
_comfyui_install_task: "asyncio.Task | None" = None


class _InstallError(Exception):
    """安装流程内部异常，携带面向小白用户的处置建议。"""

    def __init__(self, message: str, hint: str):
        super().__init__(message)
        self.message = message
        self.hint = hint


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
                "action_hint": (
                    "点击「安装」自动下载并安装 ComfyUI（含 CUDA 版 PyTorch），"
                    "首次安装约需 10-30 分钟，取决于网速"
                ),
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
        "  - comfyui/install: 一键拉取安装 ComfyUI（异步，返回后轮询 install-status）\n"
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

    elif op == "install":
        return await _install_comfyui()

    return {
        "success": False,
        "error_code": "13003",
        "message": f"ComfyUI 不支持操作 {op}",
        "detail": {},
    }


# ================================================================
# ComfyUI 一键安装实现
# ================================================================

def _install_stage(stage: str) -> tuple[int, int, str]:
    """取阶段的进度区间与中文标签，未知阶段回落到全区间。"""
    return _INSTALL_STAGES.get(stage, (0, 100, stage))


def _set_install_state(**kwargs: Any) -> None:
    """局部更新安装状态（仅覆盖传入字段）。"""
    _comfyui_install_state.update(kwargs)


def _enter_stage(stage_key: str, message: str = "") -> None:
    """
    切换阶段：进度归位到该阶段区间下界，并同步中文文案。

    注意 stage 存中文（前端直接展示），stage_key 存机器枚举。
    """
    lo, _hi, label = _install_stage(stage_key)
    _set_install_state(
        stage_key=stage_key,
        stage=label,
        stage_label=label,
        progress=lo,
        message=message or label,
    )
    logger.info(
        f"[comfyui-install] 进入阶段 {stage_key}（{label}）"
        f"{('- ' + message) if message else ''}"
    )


def _mark_install_failed(message: str, hint: str) -> None:
    """
    统一写入安装失败态。

    关键点：前端错误分支只渲染 message（不读 hint），
    因此这里必须把处置建议合并进 message，否则用户只看到"为什么失败"、
    看不到"该怎么办"。原始技术原因另存 raw_message 供排障。
    """
    label = _install_stage("failed")[2]
    _set_install_state(
        status="error",
        stage_key="failed",
        stage=label,
        stage_label=label,
        message=f"{message}。{hint}" if hint else message,
        raw_message=message,
        hint=hint,
        error_code=_ERR_COMFYUI_INSTALL_FAILED,
        finished_at=datetime.now().isoformat(),
    )


def _append_install_log(line: str) -> None:
    """追加一行安装日志（尾部截断，避免内存无界增长）。"""
    tail = _comfyui_install_state.get("log_tail")
    if not isinstance(tail, list):
        tail = []
        _comfyui_install_state["log_tail"] = tail
    tail.append(line[:300])
    if len(tail) > _INSTALL_LOG_TAIL_MAX:
        del tail[: len(tail) - _INSTALL_LOG_TAIL_MAX]


def _resolve_launch_python() -> tuple[str, str | None]:
    """
    解析"实际会用来启动 ComfyUI 的 Python 解释器"。

    关键约束：依赖必须装进 process_manager 启动 ComfyUI 时用的那个解释器，
    否则 ComfyUI 会以 ModuleNotFoundError 崩溃。process_manager 使用的是
    settings.python_executable，因此这里以它为准。

    返回：(解释器路径, 风险提示或 None)
    """
    candidate = (settings.python_executable or "python").strip()

    resolved: str | None = None
    # 情况 1：配置的是显式路径（含分隔符或直接存在）
    if any(sep in candidate for sep in ("/", "\\")) or Path(candidate).exists():
        p = Path(candidate)
        if p.exists():
            resolved = str(p)
    # 情况 2：配置的是命令名，从 PATH 解析
    if resolved is None:
        which = shutil.which(candidate)
        if which:
            resolved = which
    # 情况 3：兜底用当前进程解释器（FastAPI 自身所在环境）
    if resolved is None:
        resolved = sys.executable

    # 版本风险提示：ComfyUI + torch 生态在 3.10~3.12 最稳
    warning: str | None = None
    try:
        r = subprocess.run(
            [resolved, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, timeout=10, text=True,
        )
        ver = (r.stdout or "").strip()
        if ver:
            major, _, minor = ver.partition(".")
            try:
                mj, mn = int(major), int(minor)
            except ValueError:
                mj, mn = 0, 0
            if (mj, mn) < (3, 10):
                warning = (
                    f"解释器版本 Python {ver} 过低（ComfyUI 需要 3.10+），"
                    "建议改用 3.10~3.12 环境"
                )
            elif (mj, mn) >= (3, 13):
                warning = (
                    f"解释器为 Python {ver}，部分 ComfyUI 依赖尚无 3.13 预编译轮子，"
                    "如安装失败请改用 Python 3.10~3.12（可通过 NEXUS_PYTHON_EXECUTABLE 指定）"
                )
    except Exception as e:
        warning = f"无法确认解释器版本（{e}）"

    if Path(resolved).resolve() != Path(sys.executable).resolve():
        logger.warning(
            f"[comfyui-install] 依赖将安装到 {resolved}（ComfyUI 启动解释器），"
            f"而非后端自身解释器 {sys.executable}"
        )
    return resolved, warning


def _has_nvidia_gpu() -> bool:
    """轻量判断是否存在 NVIDIA 显卡（只看 nvidia-smi 是否可执行）。"""
    return shutil.which("nvidia-smi") is not None


def _resolve_mirrors() -> dict[str, str | None]:
    """读取镜像/代理相关环境变量。"""
    return {
        "git_repo": os.getenv("COMFYUI_GIT_MIRROR") or None,
        "pip_index_url": os.getenv("PIP_INDEX_URL") or None,
        "torch_index_url": (
            os.getenv("TORCH_INDEX_URL")
            or os.getenv("TORCH_CUDA_INDEX_URL")
            or None
        ),
    }


async def _progress_heartbeat(stage: str, expected_seconds: float) -> None:
    """
    进度心跳：在阶段区间内随时间线性推进（封顶 95% 区间宽度）。

    必要性：git clone / pip 的输出是突发式的（git 在管道模式下不打印进度），
    纯靠输出行数驱动进度会让前端进度条长时间"卡住"，用户以为程序死了。
    """
    lo, hi, _label = _install_stage(stage)
    started = time.monotonic()
    try:
        while True:
            await asyncio.sleep(1.0)
            elapsed = time.monotonic() - started
            frac = min(0.95, elapsed / max(expected_seconds * 1.15, 1.0))
            pct = lo + int((hi - lo) * frac)
            cur = _comfyui_install_state.get("progress") or 0
            if pct > cur:
                _set_install_state(progress=pct)
    except asyncio.CancelledError:
        raise


async def _run_install_step(
    cmd: list[str],
    *,
    stage: str,
    timeout: int,
    expected_seconds: float,
    cwd: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> None:
    """
    执行一条安装命令，流式读取输出并实时刷新进度状态。

    - stderr 合并进 stdout，保证报错信息一定进入 log_tail
    - 超时后 kill 子进程，避免僵尸进程占住磁盘/网络
    - 返回码非 0 时抛 _InstallError（附带最后几行输出作为根因线索）
    """
    logger.info(f"[comfyui-install] 执行：{' '.join(cmd)}")
    _append_install_log(f"$ {' '.join(cmd)}")

    env = os.environ.copy()
    # 禁止 git 弹出凭据交互，否则子进程会挂死直到超时
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("PYTHONUNBUFFERED", "1")
    if env_extra:
        env.update(env_extra)

    kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.STDOUT,
        "env": env,
    }
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

    try:
        proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
    except FileNotFoundError:
        raise _InstallError(
            f"命令不存在：{cmd[0]}",
            f"未找到 {cmd[0]}，请确认已安装并加入系统 PATH",
        )
    except Exception as e:
        raise _InstallError(f"启动命令失败：{e}", "请检查系统权限与磁盘状态")

    heartbeat = asyncio.create_task(_progress_heartbeat(stage, expected_seconds))

    async def _pump() -> int:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            # pip/git 可能用 \r 刷新同一行，按 \r 再切一次取最后一段
            text = raw.decode("utf-8", errors="replace").replace("\r", "\n")
            for piece in text.split("\n"):
                line = piece.strip()
                if not line:
                    continue
                _append_install_log(line)
                _set_install_state(message=line[:180])
        return await proc.wait()

    try:
        returncode = await asyncio.wait_for(_pump(), timeout=timeout)
    except asyncio.TimeoutError:
        _append_install_log(f"[超时] 该步骤超过 {timeout} 秒未完成，已终止")
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=10)
        except Exception:
            pass
        raise _InstallError(
            f"步骤超时（{timeout} 秒）：{cmd[0]}",
            "网络超时。请检查代理/网络，或配置镜像源后重试："
            "COMFYUI_GIT_MIRROR（源码）、PIP_INDEX_URL（依赖）、TORCH_INDEX_URL（PyTorch）",
        )
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except (asyncio.CancelledError, Exception):
            pass

    if returncode != 0:
        tail = _comfyui_install_state.get("log_tail") or []
        detail_lines = " / ".join(str(x) for x in tail[-5:])
        raise _InstallError(
            f"命令返回非 0（exit={returncode}）：{' '.join(cmd[:3])}…",
            _diagnose_install_failure(detail_lines, stage=stage),
        )


def _diagnose_install_failure(log_tail_text: str, stage: str = "") -> str:
    """
    根据子进程输出给出根因判断（现象 → 处置），避免只抛一句"安装失败"。

    未命中已知模式时按阶段给出兜底建议，保证用户永远拿到"下一步该做什么"。
    """
    t = log_tail_text.lower()
    if any(k in t for k in ("does not appear to be a git repository",
                            "repository not found",
                            "could not read from remote repository")):
        return (
            "Git 仓库地址不可用。若配置过 COMFYUI_GIT_MIRROR，请检查地址是否填错；"
            "清空该环境变量即可回退到官方地址重试"
        )
    if any(k in t for k in ("could not resolve host", "failed to connect", "timed out",
                            "connection reset", "ssl", "proxy")):
        return (
            "网络无法访问 GitHub / PyPI。请检查代理，或设置镜像源："
            "COMFYUI_GIT_MIRROR、PIP_INDEX_URL（如清华源 "
            "https://pypi.tuna.tsinghua.edu.cn/simple）"
        )
    if "no space left" in t or "not enough space" in t or "disk full" in t:
        return "磁盘空间不足。ComfyUI + PyTorch 需要约 10GB 可用空间，请清理磁盘后重试"
    if "permission denied" in t or "access is denied" in t or "winerror 5" in t:
        return "文件权限不足。请关闭正在占用该目录的程序，或以管理员身份重新运行 NexusVideo"
    if "already exists and is not an empty directory" in t:
        return "目标目录已存在且非空。请删除该目录后重试"
    if "no matching distribution" in t or "could not find a version" in t:
        return (
            "依赖轮子与当前 Python 版本不匹配。建议改用 Python 3.10~3.12 环境"
            "（通过 NEXUS_PYTHON_EXECUTABLE 指定解释器）后重试"
        )
    if "killed" in t or "memory" in t:
        return "内存不足导致依赖编译被终止。请关闭其他大内存程序后重试"

    # 兜底：按阶段给出方向性建议，避免把裸日志丢给小白用户
    if stage == "clone":
        return (
            "ComfyUI 源码下载失败。请检查网络连接，或配置 COMFYUI_GIT_MIRROR "
            "使用国内镜像后重试"
        )
    if stage in ("torch", "requirements"):
        return (
            "依赖安装失败。请检查网络连接，或配置 PIP_INDEX_URL 使用国内镜像"
            "（如 https://pypi.tuna.tsinghua.edu.cn/simple）后重试"
        )
    return f"安装未成功，请查看安装日志排查。最后输出：{log_tail_text[:200]}"


def _comfyui_entry_exists() -> bool:
    """判断 ComfyUI 是否已安装（以入口文件 main.py 为准）。"""
    return (_COMFYUI_DIR / settings.comfyui_entry).exists()


async def _install_comfyui() -> dict:
    """
    一键安装 ComfyUI 入口（非阻塞）。

    立即返回，真正的下载/安装在后台 task 中执行；
    前端通过 GET /components/comfyui/install-status 轮询进度。

    返回体同时带 data 与 error 字段，兼容前端现有解包逻辑
    （前端只读 data.status / data.message）。
    """
    global _comfyui_install_task

    # --- 幂等 1：已在安装中 → 返回当前进度，不起第二个任务 ---
    if _comfyui_install_task is not None and not _comfyui_install_task.done():
        return {
            "success": True,
            "data": {
                "status": "installing",
                "message": (
                    f"正在安装：{_comfyui_install_state.get('stage') or '准备中'}"
                    f"（{_comfyui_install_state.get('progress', 0)}%）"
                ),
                "progress": _comfyui_install_state.get("progress", 0),
                "stage": _comfyui_install_state.get("stage"),
                "poll_url": "/api/v1/settings/components/comfyui/install-status",
            },
        }

    # --- 幂等 2：已安装 → 秒返回 ---
    if _comfyui_entry_exists():
        _set_install_state(
            status="done",
            progress=100,
            stage_key="done",
            stage=_install_stage("done")[2],
            stage_label=_install_stage("done")[2],
            message="ComfyUI 已安装",
            path=str(_COMFYUI_DIR),
            error_code=None,
            hint=None,
        )
        return {
            "success": True,
            "data": {
                "status": "already_installed",
                "message": f"ComfyUI 已安装，可直接启动（{_COMFYUI_DIR}）",
                "path": str(_COMFYUI_DIR),
            },
        }

    # --- 前置检查：目录存在但非空且无 main.py → 残留目录，git clone 必失败 ---
    if _COMFYUI_DIR.exists():
        try:
            not_empty = any(_COMFYUI_DIR.iterdir())
        except Exception as e:
            return _install_error_response(
                f"无法读取目标目录 {_COMFYUI_DIR}：{e}",
                "请检查目录权限，或在设置中改用其他安装路径",
            )
        if not_empty:
            return _install_error_response(
                f"目标目录已存在但不是完整的 ComfyUI：{_COMFYUI_DIR}",
                f"检测到残留文件且缺少 {settings.comfyui_entry}。"
                f"请手动删除目录 {_COMFYUI_DIR} 后重新点击安装",
            )

    # --- 前置检查：git 是否可用 ---
    if shutil.which("git") is None:
        return _install_error_response(
            "未检测到 git 命令",
            "一键安装需要 Git。请先安装 Git（https://git-scm.com/downloads），"
            "安装时保持默认选项（自动加入 PATH），完成后重启 NexusVideo",
        )

    # --- 前置检查：磁盘空间（源码 + torch 约需 10GB） ---
    try:
        target_probe = _COMFYUI_DIR.parent if not _COMFYUI_DIR.exists() else _COMFYUI_DIR
        free_gb = shutil.disk_usage(str(target_probe)).free / (1024 ** 3)
        if free_gb < 10:
            return _install_error_response(
                f"磁盘可用空间不足（剩余 {free_gb:.1f}GB）",
                "ComfyUI 源码 + PyTorch(CUDA) 约需 10GB 可用空间，请清理磁盘后重试",
            )
    except Exception as e:
        logger.warning(f"[comfyui-install] 磁盘空间检查跳过：{e}")

    # --- 重置状态并启动后台安装任务 ---
    mirrors = _resolve_mirrors()
    _comfyui_install_state.update({
        "status": "installing",
        "progress": 0,
        "stage_key": "precheck",
        "stage": _install_stage("precheck")[2],
        "stage_label": _install_stage("precheck")[2],
        "message": "正在检查安装环境…",
        "hint": None,
        "error_code": None,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "path": str(_COMFYUI_DIR),
        "python_executable": None,
        "python_warning": None,
        "cuda_mode": None,
        "torch_version": None,
        "cuda_available": None,
        "mirrors": mirrors,
        "log_tail": [],
    })

    _comfyui_install_task = asyncio.create_task(_comfyui_install_worker())
    logger.info(f"[comfyui-install] 安装任务已启动，目标目录：{_COMFYUI_DIR}")

    return {
        "success": True,
        "data": {
            "status": "installing",
            "message": (
                "已开始安装 ComfyUI。将依次完成：下载源码 → 安装 PyTorch → 安装依赖，"
                "首次安装约需 10-30 分钟（取决于网速），期间请保持网络连接"
            ),
            "progress": 0,
            "stage": "precheck",
            "path": str(_COMFYUI_DIR),
            "poll_url": "/api/v1/settings/components/comfyui/install-status",
            "poll_interval_ms": 1500,
        },
    }


def _install_error_response(message: str, hint: str) -> dict:
    """
    构造安装失败响应，并同步写入全局状态。

    同时提供 data 字段：前端现有 executeComponentAction 只解包 data.status/
    data.message，带上后用户能看到具体原因而不是"操作结果未知"。
    """
    logger.error(f"[comfyui-install] 失败：{message} | 建议：{hint}")
    _mark_install_failed(message, hint)
    return {
        "success": False,
        "error_code": _ERR_COMFYUI_INSTALL_FAILED,
        "message": message,
        "detail": {
            "hint": hint,
            "suggested_action": "settings",
            "error_kind": "comfyui_install_failed",
            "component_id": "comfyui",
            "path": str(_COMFYUI_DIR),
        },
        # 兼容前端现有解包逻辑
        "data": {
            "status": "install_failed",
            "message": f"{message}。{hint}",
        },
    }


async def _comfyui_install_worker() -> None:
    """
    后台安装主流程：clone → torch(CUDA) → requirements → verify。

    设计要点：
      1. 全程只更新 _comfyui_install_state，不抛异常到事件循环
      2. torch 必须先于 requirements.txt 安装 —— ComfyUI 的 requirements.txt
         里也有 torch，若先跑它 pip 会拉 CPU 版轮子，导致装完无法用 GPU
      3. 失败时写入根因诊断 hint，前端直接展示可执行的处置建议
    """
    mirrors = _resolve_mirrors()
    try:
        # ---------- 阶段 1：环境检查 ----------
        _enter_stage("precheck", "正在检查 Python 解释器与显卡…")
        launch_python, py_warning = await asyncio.to_thread(_resolve_launch_python)
        has_gpu = await asyncio.to_thread(_has_nvidia_gpu)
        _set_install_state(
            python_executable=launch_python,
            python_warning=py_warning,
            cuda_mode="cuda" if has_gpu else "cpu",
            # 以 worker 内实际读到的镜像配置为准（worker 可能被独立调用）
            mirrors=mirrors,
            path=str(_COMFYUI_DIR),
        )
        if py_warning:
            _append_install_log(f"[提示] {py_warning}")
        _append_install_log(
            f"[信息] 依赖安装目标解释器：{launch_python}；"
            f"{'检测到 NVIDIA 显卡，将安装 CUDA 版 PyTorch' if has_gpu else '未检测到 NVIDIA 显卡，将安装 CPU 版 PyTorch'}"
        )

        # ---------- 阶段 2：git clone ----------
        _enter_stage("clone", "正在下载 ComfyUI 源码…")
        repo = mirrors["git_repo"] or _COMFYUI_REPO_DEFAULT
        if mirrors["git_repo"]:
            _append_install_log(f"[信息] 使用 Git 镜像：{repo}")
        try:
            _append_install_log(f"[信息] 安装目标（绝对路径）：{_COMFYUI_DIR.resolve()}")
        except Exception:
            pass
        _COMFYUI_DIR.parent.mkdir(parents=True, exist_ok=True)
        await _run_install_step(
            ["git", "clone", "--depth", "1", "--single-branch", repo, str(_COMFYUI_DIR)],
            stage="clone",
            timeout=_TIMEOUT_GIT_CLONE,
            expected_seconds=_ETA_GIT_CLONE,
        )
        if not _comfyui_entry_exists():
            raise _InstallError(
                f"源码下载完成但缺少入口文件 {settings.comfyui_entry}",
                "仓库内容异常。请删除安装目录后重试，或改用官方仓库地址（清空 COMFYUI_GIT_MIRROR）",
            )

        # ---------- 阶段 3：PyTorch（先装，避免被 CPU 版覆盖） ----------
        _enter_stage(
            "torch",
            "正在安装 PyTorch（CUDA 加速版，约 2.5GB）…" if has_gpu
            else "正在安装 PyTorch（CPU 版）…",
        )
        torch_cmd = [
            launch_python, "-m", "pip", "install",
            "--no-input", "--disable-pip-version-check", "--progress-bar", "off",
            "torch", "torchvision", "torchaudio",
        ]
        if has_gpu:
            torch_index = mirrors["torch_index_url"] or _TORCH_CUDA_INDEX_DEFAULT
            torch_cmd += ["--index-url", torch_index]
            _append_install_log(f"[信息] PyTorch 轮子索引：{torch_index}")
        elif mirrors["pip_index_url"]:
            torch_cmd += ["--index-url", mirrors["pip_index_url"]]
        await _run_install_step(
            torch_cmd,
            stage="torch",
            timeout=_TIMEOUT_TORCH,
            expected_seconds=_ETA_TORCH,
        )

        # ---------- 阶段 4：requirements.txt ----------
        _enter_stage("requirements", "正在安装 ComfyUI 依赖库…")
        req_file = _COMFYUI_DIR / "requirements.txt"
        if req_file.exists():
            req_cmd = [
                launch_python, "-m", "pip", "install",
                "--no-input", "--disable-pip-version-check", "--progress-bar", "off",
                "-r", "requirements.txt",
            ]
            if mirrors["pip_index_url"]:
                req_cmd += ["--index-url", mirrors["pip_index_url"]]
                _append_install_log(f"[信息] pip 镜像：{mirrors['pip_index_url']}")
            await _run_install_step(
                req_cmd,
                stage="requirements",
                timeout=_TIMEOUT_REQUIREMENTS,
                expected_seconds=_ETA_REQUIREMENTS,
                cwd=_COMFYUI_DIR,
            )
        else:
            _append_install_log("[警告] 未找到 requirements.txt，跳过依赖安装")

        # ---------- 阶段 5：结果校验 ----------
        _enter_stage("verify", "正在校验安装结果…")
        if not _comfyui_entry_exists():
            raise _InstallError(
                f"校验失败：未找到 {_COMFYUI_DIR / settings.comfyui_entry}",
                "安装目录不完整，请删除后重新安装",
            )
        torch_version, cuda_available = await _verify_torch(launch_python)
        _set_install_state(torch_version=torch_version, cuda_available=cuda_available)
        if has_gpu and cuda_available is False:
            _append_install_log(
                "[警告] torch 已安装但 torch.cuda.is_available() 为 False，"
                "生成将退化为 CPU（极慢）"
            )

        # ---------- 完成 ----------
        # 安装后模型目录应指向 ComfyUI 内部 models/，同步刷新模块级缓存，
        # 避免用户装完 ComfyUI 后模型检测仍扫描旧目录。
        global _MODELS_DIR
        _MODELS_DIR = _COMFYUI_DIR / "models"

        ok_msg = f"ComfyUI 安装完成（{_COMFYUI_DIR}）"
        if torch_version:
            ok_msg += f"，PyTorch {torch_version}"
            ok_msg += "，CUDA 加速可用" if cuda_available else "，CUDA 不可用（将使用 CPU）"
        _set_install_state(
            status="done",
            stage_key="done",
            stage=_install_stage("done")[2],
            stage_label=_install_stage("done")[2],
            progress=100,
            message=ok_msg,
            hint=None,
            error_code=None,
            finished_at=datetime.now().isoformat(),
        )
        _append_install_log(f"[完成] {ok_msg}")
        logger.info(f"[comfyui-install] {ok_msg}")

    except _InstallError as e:
        _mark_install_failed(e.message, e.hint)
        _append_install_log(f"[失败] {e.message}")
        logger.error(f"[comfyui-install] 安装失败：{e.message} | 建议：{e.hint}")
    except asyncio.CancelledError:
        _mark_install_failed(
            "安装已取消",
            "安装被中断（可能是后端重启）。请重新点击安装",
        )
        logger.warning("[comfyui-install] 安装任务被取消")
        raise
    except Exception as e:
        _mark_install_failed(f"安装过程异常：{e}", _diagnose_install_failure(str(e)))
        _append_install_log(f"[异常] {e}")
        logger.exception(f"[comfyui-install] 安装过程未预期异常：{e}")


async def _verify_torch(launch_python: str) -> tuple[str | None, bool | None]:
    """
    校验 torch 是否可用及 CUDA 是否就绪（非致命：失败只记录不阻断）。

    这是排查"装完却跑不动 GPU"的第一现场证据。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            launch_python, "-c",
            "import torch,json;print(json.dumps({'v':torch.__version__,"
            "'cuda':bool(torch.cuda.is_available())}))",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **({"creationflags": 0x08000000} if sys.platform == "win32" else {}),
        )
        out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_VERIFY)
        out = (out_bytes or b"").decode("utf-8", errors="replace").strip()
        _append_install_log(f"[校验] torch 探测输出：{out[-200:]}")
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                import json
                data = json.loads(line)
                return str(data.get("v")), bool(data.get("cuda"))
        return None, None
    except Exception as e:
        _append_install_log(f"[校验] torch 探测失败：{e}")
        logger.warning(f"[comfyui-install] torch 校验失败（不阻断）：{e}")
        return None, None


@router.get(
    "/components/comfyui/install-status",
    summary="ComfyUI 安装进度",
    description=(
        "轮询 ComfyUI 一键安装进度。\n\n"
        "调用方式：POST /components/comfyui/action {\"action\":\"install\"} 启动安装后，"
        "按 2 秒间隔轮询本接口，直到 status 为 done 或 error 即停止轮询。\n\n"
        "status:    idle（未开始）| installing（安装中）| done（成功）| error（失败）\n"
        "progress:  0-100\n"
        "stage:     中文阶段文案，可直接展示（如「安装 PyTorch（CUDA 加速）」）\n"
        "stage_key: 机器可读阶段枚举 precheck|clone|torch|requirements|verify|done|failed\n"
        "message:   当前动作描述；失败时为「原因 + 处置建议」\n"
        "hint:      失败时的处置建议（message 已包含，单独提供便于分开展示）\n"
        "log_tail:  最近 60 行安装日志，用于排障"
    ),
)
async def get_comfyui_install_status() -> dict:
    """返回 ComfyUI 安装任务的当前状态快照。"""
    state = dict(_comfyui_install_state)
    state["log_tail"] = list(state.get("log_tail") or [])
    state["installed"] = _comfyui_entry_exists()
    state["comfyui_path"] = str(_COMFYUI_DIR)
    # 供前端判断是否需要继续轮询（等价于 status == "installing"）
    state["is_running"] = state.get("status") == "installing"
    return {"success": True, "data": state}


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
