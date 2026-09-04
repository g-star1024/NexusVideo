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
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import psutil
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from loguru import logger

from config import settings
from exceptions import ErrorCode
from core.vram import _get_vram_total_mb, _has_nvidia_gpu

router = APIRouter(prefix="/api/v1/settings", tags=["设置中心"])

# ================================================================
# 子进程输出解码：中文 Windows 关键兼容点
# ================================================================
# 现象：nvidia-smi / wmic / powershell / pip 在中文 Windows（GBK 代码页）上
#       会输出非 UTF-8 字节（典型为 0xC1 开头的 GBK 中文，出现在 nvidia-smi
#       的进程列表段、pip 的进度行）。
# 坑点：subprocess.run(..., text=True) 使用 locale 编码**严格**解码，一旦
#       遇到非法字节，异常抛在内部读取线程里，子进程结果对象仍"正常返回"，
#       但 result.stdout 会被置为 None。上层 `result.stdout.split(...)` 随即
#       AttributeError，被 except 吞掉后表现为「CUDA unknown / 字段为空」。
# 处置：统一改用 errors="replace"，保证 stdout 永远是 str（不会是 None）。
#       代价：个别中文字符变替换符，但不影响 ASCII 关键字段（版本号/数值）解析。
_TEXT_KW: dict[str, Any] = {"encoding": "utf-8", "errors": "replace"}

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
#
# 新增字段（Task #5：模型列表显存过滤 + lowvram 自动策略）：
#   min_vram_mb: 运行该模型所需的最低显存（MB）。用于按本机实际显存过滤/标注。
#   recommended: 是否为「默认推荐」的轻量模型（前端据此决定默认展示分组）。
#                True 的模型（Wan2.1 T2V 1.3B / AnimateDiff）作为默认展示项；
#                False 的较重模型（CogVideoX-5b / Wan2.1 I2V 14B）归入「高级/不推荐」。
#
# 数值依据（fp16 推理经验值）：
#   - Wan2.1-T2V-1.3B：约 6GB 可跑（配合 --lowvram 更低）
#   - AnimateDiff（SD1.5 + 运动模块）：约 4GB
#   - CogVideoX-5b：约 12GB
#   - Wan2.1-I2V-14B：约 16GB
_MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "model_cogvideox": {
        "name": "CogVideoX 模型（文生视频）",
        "icon": "model",
        "size_gb": 12.5,
        "min_vram_mb": 12288,
        "recommended": False,
        "patterns": ["cogvideox*.safetensors", "cogvideox*.ckpt"],
        "download_url": "https://huggingface.co/THUDM/CogVideoX-5b",
        "detail": "文生视频 CogVideoX-5b，约 12.5GB",
    },
    "model_wan21_t2v": {
        "name": "Wan2.1 T2V 模型（文生视频）",
        "icon": "model",
        "size_gb": 5.6,
        "min_vram_mb": 6144,
        "recommended": True,
        "patterns": ["wan2.1*t2v*.safetensors", "wan2.1*t2v*.fp16.safetensors"],
        "download_url": "https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B",
        "detail": "文生视频 Wan2.1 T2V 1.3B fp16，约 5.6GB（主力模型）",
    },
    "model_wan21_i2v": {
        "name": "Wan2.1 I2V 模型（图生视频）",
        "icon": "model",
        "size_gb": 5.6,
        "min_vram_mb": 16384,
        "recommended": False,
        "patterns": ["wan2.1*i2v*.safetensors", "wan2.1*i2v*.fp16.safetensors"],
        "download_url": "https://huggingface.co/Wan-AI/Wan2.1-I2V-14B",
        "detail": "图生视频 Wan2.1 I2V 14B，约 28GB",
    },
    "model_animatediff": {
        "name": "AnimateDiff 模型（保底）",
        "icon": "model",
        "size_gb": 3.5,
        "min_vram_mb": 4096,
        "recommended": True,
        "patterns": ["animatediff*.safetensors", "mm_sd*.safetensors"],
        "download_url": "https://huggingface.co/guoyww/animatediff",
        "detail": "AnimateDiff 动画模型，约 3.5GB（显存不足时的保底方案）",
    },
}


# ================================================================
# 模型显存过滤 / 标注辅助函数
# ================================================================
def _model_group(reg: dict[str, Any]) -> str:
    """模型分组：recommended=推荐（默认展示），advanced=高级/不推荐。"""
    return "recommended" if reg.get("recommended", False) else "advanced"


def _attach_vram_warning(comp: dict[str, Any], reg: dict[str, Any]) -> None:
    """
    根据本机实际显存给模型组件附加显存标注（原地修改 comp）。

    - vram_warning=True 表示本机显存不满足该模型的最低要求；
    - 此时 vram_note 给出中文提示，且 group 强制落到 advanced、recommended 置 False；
    - 无 NVIDIA 显卡（nvidia-smi 不可用）时，所有需要显存的模型都标 warning。
    """
    min_vram = reg.get("min_vram_mb", 0)
    vram = _get_vram_total_mb()
    if vram is None:
        # 无可用 GPU：GPU 视频生成不可用，需要显存的模型一律标 warning
        comp["vram_warning"] = bool(min_vram)
        comp["vram_note"] = (
            "未检测到 NVIDIA 显卡，无法运行 GPU 视频生成"
            if min_vram else None
        )
        if min_vram:
            comp["group"] = "advanced"
            comp["recommended"] = False
        return
    if min_vram and vram < min_vram:
        comp["vram_warning"] = True
        need_gb = round(min_vram / 1024, 1)
        cur_gb = round(vram / 1024, 1)
        comp["vram_note"] = (
            f"需 ≥{need_gb}GB 显存，当前设备仅 {cur_gb}GB，运行可能爆显存"
        )
        comp["group"] = "advanced"
        comp["recommended"] = False
    else:
        comp["vram_warning"] = False
        comp["vram_note"] = None


def _build_model_comp_base(model_id: str, reg: dict[str, Any]) -> dict[str, Any]:
    """构造模型组件的基础字段（含显存过滤标注），供各分支复用。"""
    comp = {
        "id": model_id,
        "name": reg.get("name", model_id),
        "icon": reg.get("icon", "model"),
        "size_gb": reg.get("size_gb", 0),
        "min_vram_mb": reg.get("min_vram_mb", 0),
        "recommended": reg.get("recommended", False),
        "group": _model_group(reg),
        "download_url": reg.get("download_url"),
        "vram_warning": False,
        "vram_note": None,
    }
    _attach_vram_warning(comp, reg)
    return comp


# ================================================================
# ComfyUI 一键安装：常量与全局状态
# ================================================================
# 官方仓库（可用 COMFYUI_GIT_MIRROR 环境变量覆盖为镜像/代理地址）
_COMFYUI_REPO_DEFAULT = "https://github.com/comfyanonymous/ComfyUI.git"

# 源码压缩包地址（无 Git / git 协议被屏蔽时的回退下载源）。
# codeload.github.com 是 GitHub 的纯 HTTPS 归档接口：只要浏览器能打开 GitHub 就能下载，
# 既不要求本机安装 Git，也不走常被公司/校园网屏蔽的 git 协议。
# 实测对照：同一台机器 `git ls-remote` 在 21 秒后 Failed to connect to github.com:443，
# 而 curl 该压缩包 HTTP 200、约 12MB、5~7MB/s 正常。
# 可用 COMFYUI_TARBALL_URL 显式覆盖为国内镜像的同源压缩包地址。
_COMFYUI_TARBALL_DEFAULT = (
    "https://codeload.github.com/comfyanonymous/ComfyUI/tar.gz/refs/heads/master"
)


def _comfyui_default_tarball_url() -> str:
    """
    按锁定版本（COMFYUI_SOURCE_TAG，默认 master）拼出 codeload 压缩包地址。

    master → refs/heads/master；指定 tag/commit → refs/tags/<tag>。
    把源码版本做成显式配置项，是为了"一键拉取"结果可复现，
    避免 master 漂移再次引入 torch 不兼容这类产品级缺陷。
    """
    tag = settings.comfyui_source_tag or "master"
    ref = f"refs/tags/{tag}" if tag != "master" else "refs/heads/master"
    return f"https://codeload.github.com/comfyanonymous/ComfyUI/tar.gz/{ref}"


# PyTorch CUDA 轮子索引与版本：与 ComfyUI 源码**显式对齐锁定**（产品级根因修复）。
#
# 根因（实测）：ComfyUI 源码（含 comfy-kitchen==0.2.31）用 PEP585 内置泛型 `list[int]`
# 标注 torch.library.custom_op；torch <2.7 的 infer_schema 只白名单 typing.List[int]，
# 直接抛 ValueError。cu124 索引的 torch 顶到 2.6.0，而 master ComfyUI 要求 ≥2.7，
# 两者组合必然起不来。因此：
#   - cu 索引从 cu124 升到 cu126：cu126 是本机已装 NVIDIA 驱动（560.94，CUDA 上限 12.6）
#     能支持的最高 CUDA 大版本（cu128 需驱动 ≥570，本机不满足）；sm_75(Turing) 各版本都支持。
#   - torch 锁 2.7.1：首个修好该问题的版本，且贴近 comfy-kitchen 0.2.31 时代，
#     避免 2.13 等过新轮子引入新的不兼容漂移。
#   均可用 NEXUS_TORCH_CUDA_INDEX_URL / TORCH_INDEX_URL / NEXUS_TORCH_VERSION / TORCH_VERSION 覆盖。
_TORCH_CUDA_INDEX_DEFAULT = "https://download.pytorch.org/whl/cu126"
_TORCH_VERSION_PIN = "2.7.1"
_TORCHVISION_VERSION_PIN = "0.22.1"
_TORCHAUDIO_VERSION_PIN = "2.7.1"

# 各阶段超时（秒）。torch 轮子约 2.5GB，慢网络下需要较长时间。
_TIMEOUT_GIT_CLONE = 300
_TIMEOUT_TORCH = 1800
_TIMEOUT_REQUIREMENTS = 900
_TIMEOUT_VERIFY = 120

# 源码压缩包下载（回退方案）的超时控制：
# 单次读超时只防"连上后卡死"，整体上限才防"慢速拖死"，两者都要有。
_TARBALL_READ_TIMEOUT = 30
_TIMEOUT_SOURCE_DOWNLOAD = 600
_TARBALL_CHUNK = 256 * 1024

# 各阶段预估耗时（秒），仅用于进度条平滑推进（非硬性约束）
_ETA_GIT_CLONE = 60.0
_ETA_SOURCE_DOWNLOAD = 90.0
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
# 已收敛为专属错误码 11008（exceptions.ErrorCode.COMFYUI_INSTALL_FAILED），
# 不再复用 11003（COMFYUI_TIMEOUT），避免"安装失败"与"任务超时"在日志/告警中混淆。
# 前端只渲染 message、且仅按 status 决定轮询停止，不依赖 error_code 数值，迁移零风险。
# 保留 detail.error_kind = "comfyui_install_failed" 作为稳定的二级判别键。
_ERR_COMFYUI_INSTALL_FAILED = ErrorCode.COMFYUI_INSTALL_FAILED

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
    "source_method": None,         # 源码实际获取方式：git | tarball（排障用）
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
                capture_output=True, timeout=5, **_TEXT_KW,
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
            capture_output=True, timeout=5, **_TEXT_KW,
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


# nvidia-smi 头部形如：
#   | NVIDIA-SMI 560.94    Driver Version: 560.94    CUDA Version: 12.6     |
# 注意：不能用 split("CUDA Version")[-1].strip() —— 会残留前导 ':' 与尾部 '|'
# （实测解析出 ': 12.6     |'）。改用正则精确提取纯版本号。
_CUDA_VERSION_RE = re.compile(r"CUDA\s+Version\s*:\s*([0-9][0-9.]*)")


def _read_nvidia_smi_text() -> str:
    """读取 nvidia-smi 完整输出。

    正常返回 str；nvidia-smi 不存在 / 超时 / 解码异常一律返回空串，绝不抛异常。
    """
    try:
        result = subprocess.run(
            ["nvidia-smi"], capture_output=True, timeout=5, **_TEXT_KW
        )
        return result.stdout or ""
    except Exception:
        return ""


def _parse_cuda_version(text: str) -> str:
    """从 nvidia-smi 输出中提取 CUDA 版本号，取不到返回 'unknown'。"""
    m = _CUDA_VERSION_RE.search(text or "")
    return m.group(1) if m else "unknown"


def _detect_gpu_driver() -> dict[str, Any]:
    """检测 NVIDIA GPU 驱动与 CUDA。"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, timeout=5, **_TEXT_KW,
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

        # 尝试获取 CUDA 版本（从 nvidia-smi 完整输出的头部解析）
        # 注意：必须用安全解码的 _TEXT_KW，否则中文 Windows 上 stdout 为 None，
        #       for 循环抛 AttributeError 被吞掉 → 永远显示 "CUDA unknown"。
        cuda_version = _parse_cuda_version(_read_nvidia_smi_text())

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


# 匹配 "ffmpeg version 9.0.1-essentials_build-..." 中的纯版本号
_FFMPEG_VERSION_RE = re.compile(r"ffmpeg\s+version\s+([0-9][0-9.]*)", re.IGNORECASE)


def _detect_ffmpeg() -> dict[str, Any]:
    """检测 FFmpeg 视频编解码器。"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, timeout=5, **_TEXT_KW,
        )
        if result.returncode == 0:
            first_line = result.stdout.split("\n")[0].strip() if result.stdout else "FFmpeg (version unknown)"
            # 解析版本。实测 Windows gyan.dev 构建的首行为：
            #   ffmpeg version 9.0.1-essentials_build-www.gyan.dev Copyright (c) ...
            # 版本号后面紧跟着 "-essentials_build-..." 后缀，按空格切词再判断是否
            # 纯数字会整词失配（旧实现因此永远返回 "unknown"）。改用正则精确匹配。
            m = _FFMPEG_VERSION_RE.search(first_line)
            version = m.group(1) if m else "unknown"
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
    """检测指定模型文件是否存在，并附带显存过滤标注。"""
    reg = _MODEL_REGISTRY.get(model_id)
    if not reg:
        return {
            "id": model_id,
            "name": "未知模型",
            "icon": "model",
            "status": "error",
            "detail": f"未知的模型 ID：{model_id}",
        }

    # 基础字段（含 min_vram_mb / recommended / group / vram_warning / vram_note）
    comp = _build_model_comp_base(model_id, reg)

    if not _MODELS_DIR.exists():
        return {
            **comp,
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
            **comp,
            "status": "ok",
            "version": file_path.name,
            "size_gb": size_gb,
            "detail": f"{reg['detail']}，已安装：{file_path.name}（{size_gb}GB）",
            "action_hint": None,
            "action_button": None,
        }
    else:
        return {
            **comp,
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
        comp = _build_model_comp_base(model_id, reg)
        comp.update({
            "status": "error",
            "version": None,
            "detail": f"模型检测异常：{e}",
            "action_hint": "检测出错，建议检查模型目录权限",
            "action_button": "修复",
        })
        return comp


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
            capture_output=True, timeout=10, **_TEXT_KW,
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


def _resolve_mirrors() -> dict[str, str | None]:
    """
    读取镜像/代理配置。

    优先级：Settings（.env 中带 NEXUS_ 前缀或裸名均可，见 config.py 的 AliasChoices）
           → 进程环境变量 os.getenv。

    保留 os.getenv 回退的理由：兼容"后端启动之后才设置环境变量"的场景，
    以及用户按旧文档直接写裸变量的习惯；两类读法结果一致，不会互相打架。
    """
    return {
        "git_repo": settings.comfyui_git_mirror or os.getenv("COMFYUI_GIT_MIRROR") or None,
        "pip_index_url": settings.pip_index_url or os.getenv("PIP_INDEX_URL") or None,
        "torch_index_url": (
            settings.torch_index_url
            or os.getenv("TORCH_INDEX_URL")
            or os.getenv("TORCH_CUDA_INDEX_URL")
            or None
        ),
        "tarball_url": (
            settings.comfyui_tarball_url or os.getenv("COMFYUI_TARBALL_URL") or None
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


def _cleanup_partial_source_dir() -> None:
    """
    清理下载/克隆失败留下的半成品目录。

    必要性：git clone 失败时仍会创建出目标目录（哪怕只有 .git），
    若不清理，后续压缩包解压/搬迁会因目录非空而失败或混入残缺文件。
    """
    if not _COMFYUI_DIR.exists():
        return
    _append_install_log(f"[信息] 正在清理未完成的残留文件：{_COMFYUI_DIR}")
    shutil.rmtree(_COMFYUI_DIR, ignore_errors=True)
    if _COMFYUI_DIR.exists():
        raise _InstallError(
            f"无法清理残留目录 {_COMFYUI_DIR}",
            "该目录可能正被其他程序占用（资源管理器、杀毒软件、终端窗口）。"
            "请关闭相关程序后手动删除该目录，再重新点击安装",
        )


def _resolve_tarball_url(mirrors: dict[str, str | None], repo: str) -> str:
    """
    决定回退下载用的源码压缩包地址。

    优先级：
      1. 显式配置 COMFYUI_TARBALL_URL / NEXUS_COMFYUI_TARBALL_URL（镜像场景首选）
      2. 仓库地址本身就是归档地址（codeload / .tar.gz / /archive/）→ 原样使用
      3. 仓库地址是 GitHub（含默认官方仓库）→ 自动转换为 codeload 归档地址
      4. 其他镜像（如 Gitee 的 git 地址，无法推导）→ 回落到官方 codeload 地址并记日志
    """
    explicit = mirrors.get("tarball_url")
    if explicit:
        return explicit

    lowered = (repo or "").lower()
    if ("codeload" in lowered or "/archive/" in lowered
            or lowered.endswith((".tar.gz", ".tgz", ".zip"))):
        return repo

    parsed = urllib.parse.urlparse(repo)
    host = (parsed.hostname or "").lower()
    if host.endswith("github.com"):
        owner_name = parsed.path.strip("/")
        if owner_name.endswith(".git"):
            owner_name = owner_name[: -len(".git")]
        if "/" in owner_name:
            tag = settings.comfyui_source_tag or "master"
            ref = f"refs/tags/{tag}" if tag != "master" else "refs/heads/master"
            return f"https://codeload.github.com/{owner_name}/tar.gz/{ref}"

    _append_install_log(
        f"[信息] 镜像地址 {repo} 无法自动推导压缩包地址，改用官方下载地址"
    )
    return _comfyui_default_tarball_url()


def _extract_tarball_safely(tar: "tarfile.TarFile", dest: Path) -> None:
    """
    安全解压：阻断 ../ 路径穿越（CVE-2007-4559 类风险）。

    Python 3.12+ 直接用官方 filter="data"；低版本手工校验每个成员的目标路径。
    """
    if sys.version_info >= (3, 12):
        tar.extractall(path=str(dest), filter="data")
        return

    root = dest.resolve()
    safe_members = []
    for member in tar.getmembers():
        rel = member.name.replace("\\", "/").lstrip("/")
        try:
            target = (root / rel).resolve()
        except Exception:
            continue
        if target == root or root in target.parents:
            member.name = rel
            safe_members.append(member)
        else:
            logger.warning(f"[comfyui-install] 跳过可疑压缩包条目：{member.name}")
    tar.extractall(path=str(dest), members=safe_members)


def _move_tree_contents(src: Path, dst: Path) -> None:
    """把 src 下的内容整体搬进 dst（目录合并、同名文件覆盖）。"""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if target.exists():
            if target.is_dir() and item.is_dir():
                _move_tree_contents(item, target)
                shutil.rmtree(item, ignore_errors=True)
                continue
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                try:
                    target.unlink()
                except Exception:
                    pass
        shutil.move(str(item), str(target))


def _download_comfyui_tarball(url: str) -> None:
    """
    下载 ComfyUI 源码压缩包并解压到 _COMFYUI_DIR（同步阻塞，必须在子线程执行）。

    - 只用标准库（urllib + tarfile），不引入新依赖
    - 逐块下载并写日志/更新 message，避免前端进度条长时间"卡死"
    - 双层超时：单次读超时 30 秒 + 整体上限 _TIMEOUT_SOURCE_DOWNLOAD 秒
    - 解压后剥离压缩包顶层目录（等价于 tar --strip-components=1）
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="nexus-comfyui-", dir=str(_COMFYUI_DIR.parent)))
    archive = tmp_dir / "ComfyUI.tar.gz"
    deadline = time.monotonic() + _TIMEOUT_SOURCE_DOWNLOAD
    last_report = 0.0
    done = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NexusVideo/1.0"})
        _append_install_log(f"$ 下载 {url}")
        with urllib.request.urlopen(req, timeout=_TARBALL_READ_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            if total:
                _append_install_log(f"[信息] 源码压缩包大小：{total / 1048576:.1f} MB")
            with open(archive, "wb") as fh:
                while True:
                    if time.monotonic() > deadline:
                        raise _InstallError(
                            f"源码下载超时（超过 {_TIMEOUT_SOURCE_DOWNLOAD} 秒）",
                            "下载速度太慢或网络不稳定。请检查代理/网络，或设置 "
                            "COMFYUI_TARBALL_URL 指向国内镜像的压缩包地址后重试",
                        )
                    chunk = resp.read(_TARBALL_CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now - last_report >= 1.0:
                        last_report = now
                        if total:
                            pct = min(99, done * 100 // total)
                            _append_install_log(
                                f"[信息] 已下载 {done / 1048576:.1f} / "
                                f"{total / 1048576:.1f} MB（{pct}%）"
                            )
                            _set_install_state(message=f"正在下载 ComfyUI 源码…{pct}%")
                        else:
                            _append_install_log(f"[信息] 已下载 {done / 1048576:.1f} MB")

        _append_install_log(f"[信息] 下载完成（{done / 1048576:.1f} MB），正在解压…")
        _set_install_state(message="正在解压 ComfyUI 源码…")
        extract_root = tmp_dir / "src"
        extract_root.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(archive, "r:gz") as tar:
                _extract_tarball_safely(tar, extract_root)
        except tarfile.TarError as e:
            raise _InstallError(
                f"源码压缩包解压失败：{e}",
                "下载到的文件不是有效的压缩包（镜像源可能返回了错误页面）。"
                "请清空 COMFYUI_TARBALL_URL / COMFYUI_GIT_MIRROR 回退到官方地址后重试",
            )

        # 剥离顶层目录（ComfyUI-master/…）后再整体搬进安装目录
        entries = list(extract_root.iterdir())
        top = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_root
        _move_tree_contents(top, _COMFYUI_DIR)
        _append_install_log(f"[信息] 源码已解压到 {_COMFYUI_DIR}")
    except _InstallError:
        raise
    except Exception as e:
        raise _InstallError(
            f"源码压缩包下载失败：{e}",
            _diagnose_install_failure(str(e), stage="clone"),
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _source_download_hint(git_available: bool, tarball_hint: str) -> str:
    """两种方式都失败时，给小白用户的合并处置建议（不出现 exit code 等术语）。"""
    if git_available:
        tried = (
            "已自动试过两种方式：① Git 克隆失败（常见原因是公司/校园网络屏蔽了 git 协议）"
            "② 改为直接下载源码压缩包，也失败了"
        )
    else:
        tried = (
            "本机没有安装 Git，已自动改用「直接下载源码压缩包」的方式（不需要安装 Git），"
            "但下载也失败了"
        )
    return (
        f"{tried}。{tarball_hint.rstrip('。')}；"
        "你也可以手动下载 ComfyUI 源码，解压后把文件放进安装目录，再点击安装。"
    )


async def _download_comfyui_source(repo: str, mirrors: dict[str, str | None]) -> str:
    """
    获取 ComfyUI 源码：方式 A（git clone）优先，失败自动回退方式 B（下载压缩包解压）。

    为什么必须有方式 B（实测结论）：
      大量小白用户的机器上根本没装 Git；公司/校园网还常屏蔽 git 协议——
      实测 `git ls-remote` 在 21 秒后 Failed to connect to github.com:443，
      而同一台机器下载 codeload 压缩包 HTTP 200、约 12MB、5~7MB/s 完全正常。
      只支持 git clone 会让「一键拉取」在这些环境 100% 失败。

    返回："git" | "tarball"（实际生效的方式，写入安装状态供排障）
    """
    git_available = shutil.which("git") is not None
    # 锁定版本：默认 master，可用 COMFYUI_SOURCE_TAG 指定 tag/commit 保证可复现
    tag = settings.comfyui_source_tag or "master"

    # ---- 方式 A：git clone ----
    if git_available:
        try:
            await _run_install_step(
                ["git", "clone", "--depth", "1", "--single-branch", "-b", tag,
                 repo, str(_COMFYUI_DIR)],
                stage="clone",
                timeout=_TIMEOUT_GIT_CLONE,
                expected_seconds=_ETA_GIT_CLONE,
            )
            return "git"
        except _InstallError as e:
            _append_install_log(f"[警告] Git 克隆未成功（{e.message}），自动改用下载压缩包方式")
        except Exception as e:
            _append_install_log(f"[警告] Git 克隆异常（{e}），自动改用下载压缩包方式")
        # git clone 会留下半成品目录，换方式前必须清掉
        _cleanup_partial_source_dir()
    else:
        _append_install_log(
            "[信息] 本机未安装 Git，已自动改用「直接下载源码压缩包」方式，无需安装 Git"
        )

    # ---- 方式 B：HTTP 下载压缩包并解压 ----
    tarball_url = _resolve_tarball_url(mirrors, repo)
    _set_install_state(message="正在下载 ComfyUI 源码压缩包…")
    heartbeat = asyncio.create_task(_progress_heartbeat("clone", _ETA_SOURCE_DOWNLOAD))
    try:
        await asyncio.to_thread(_download_comfyui_tarball, tarball_url)
    except _InstallError as e:
        _cleanup_partial_source_dir()
        raise _InstallError(
            "ComfyUI 源码下载失败",
            _source_download_hint(git_available, e.hint),
        )
    except Exception as e:
        _cleanup_partial_source_dir()
        raise _InstallError(
            "ComfyUI 源码下载失败",
            _source_download_hint(git_available, _diagnose_install_failure(str(e), stage="clone")),
        )
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except (asyncio.CancelledError, Exception):
            pass

    return "tarball"


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

    # --- 前置提示：git 是否可用（已是软性条件，不再拦截） ---
    # 没装 Git 也能装：阶段 2 会自动回退为「下载源码压缩包并解压」。
    # 旧版本在此直接报错拦截，导致没装 Git 的小白用户根本走不到安装流程。
    if shutil.which("git") is None:
        logger.info(
            "[comfyui-install] 未检测到 git 命令，将改用下载源码压缩包方式获取 ComfyUI"
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
    # 前端错误分支只渲染 message（不读 hint），因此顶层 message 也必须把处置建议合并进去，
    # 与 data.message、轮询态 _comfyui_install_state["message"] 保持一致。
    full_message = f"{message}。{hint}" if hint else message
    return {
        "success": False,
        "error_code": _ERR_COMFYUI_INSTALL_FAILED,
        "message": full_message,
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
            "message": full_message,
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

        # ---------- 阶段 2：获取源码（git clone，失败自动回退为下载压缩包） ----------
        _enter_stage("clone", "正在下载 ComfyUI 源码…")
        repo = mirrors["git_repo"] or _COMFYUI_REPO_DEFAULT
        if mirrors["git_repo"]:
            _append_install_log(f"[信息] 使用源码镜像：{repo}")
        try:
            _append_install_log(f"[信息] 安装目标（绝对路径）：{_COMFYUI_DIR.resolve()}")
        except Exception:
            pass
        _COMFYUI_DIR.parent.mkdir(parents=True, exist_ok=True)
        source_method = await _download_comfyui_source(repo, mirrors)
        _set_install_state(source_method=source_method)
        _append_install_log(f"[信息] 源码获取方式：{source_method}")

        # 两种方式最终都要过这一关：入口文件不存在 = 源码没拿到
        if not _comfyui_entry_exists():
            raise _InstallError(
                f"源码下载完成但缺少入口文件 {settings.comfyui_entry}",
                "拿到的源码不完整（可能是镜像源缺文件、或下载中途被截断）。"
                "请重新点击安装；若反复失败，请清空 COMFYUI_GIT_MIRROR 与 "
                "COMFYUI_TARBALL_URL 回退到官方地址",
            )

        # ---------- 阶段 3：PyTorch（先装，避免被 CPU 版覆盖） ----------
        _enter_stage(
            "torch",
            "正在安装 PyTorch（CUDA 加速版，约 2.5GB）…" if has_gpu
            else "正在安装 PyTorch（CPU 版）…",
        )
        # 锁定版本：torch 与 ComfyUI 源码必须对齐。裸 `torch` 会拉到 cu124 的 2.6.0，
        # 与 master ComfyUI（comfy-kitchen==0.2.31）不兼容、必然起不来；此处显式钉死版本。
        torch_ver = settings.torch_version or _TORCH_VERSION_PIN
        torchvision_ver = _TORCHVISION_VERSION_PIN
        torchaudio_ver = _TORCHAUDIO_VERSION_PIN
        torch_cmd = [
            launch_python, "-m", "pip", "install",
            "--no-input", "--disable-pip-version-check", "--progress-bar", "off",
            f"torch=={torch_ver}",
            f"torchvision=={torchvision_ver}",
            f"torchaudio=={torchaudio_ver}",
        ]
        if has_gpu:
            torch_index = mirrors["torch_index_url"] or _TORCH_CUDA_INDEX_DEFAULT
            torch_cmd += ["--index-url", torch_index]
            _append_install_log(
                f"[信息] PyTorch 轮子索引：{torch_index}（锁定 torch=={torch_ver}, "
                f"torchvision=={torchvision_ver}, torchaudio=={torchaudio_ver}）"
            )
        elif mirrors["pip_index_url"]:
            torch_cmd += ["--index-url", mirrors["pip_index_url"]]
            _append_install_log(
                f"[信息] 无 GPU，使用默认源安装 torch=={torch_ver}（CPU 轮子）"
            )
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

        # ---------- 关键：启动前版本兼容自检 ----------
        # 直接 import comfy.utils（即最初崩溃的那一行）。通过 = torch 与 ComfyUI
        # 版本兼容；不通过 = 给好人话错误 + 明确修复动作，而不是甩 exit code=1 给用户。
        _append_install_log("[信息] 正在自检 ComfyUI 关键模块导入（torch/ComfyUI 版本兼容）…")
        imports_ok, import_detail = await asyncio.to_thread(
            _verify_comfy_imports, launch_python
        )
        if not imports_ok:
            raise _InstallError(
                "ComfyUI 关键模块导入失败（torch 与 ComfyUI 版本不兼容）",
                "ComfyUI 依赖的 PyTorch 版本与源码不匹配：典型是 torch 版本过低，无法加载 "
                "comfy-kitchen 等依赖（报错多为 list[int] 这类类型标注不兼容）。"
                "请确认 NEXUS_TORCH_VERSION 锁定为 2.7.x，且镜像源提供对应 cu126 的 PyTorch 轮子；"
                "若仍失败，请删除 .venv-comfyui 后重新点击「一键拉取」。"
                f"（诊断：{import_detail[-400:] if import_detail else '无'}）",
            )
        _append_install_log("[信息] ComfyUI 关键模块导入自检通过，版本兼容")

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


def _verify_comfy_imports(launch_python: str) -> tuple[bool, str]:
    """
    真正的版本兼容自检：在 ComfyUI 启动**之前**，子进程跑 `import comfy.utils`。

    这正是最初崩溃的那一行（torch 2.6 下 comfy-kitchen 用 list[int] 标注
    torch.library.custom_op 被 infer_schema 拒绝）。能过 = torch 版本与 ComfyUI
    源码兼容；过不了 = 直接报人话，而不是把 exit code=1 甩给用户。

    返回 (ok, 诊断信息)。
    """
    code = "import comfy.utils, comfy.model_management; print('COMFY_IMPORT_OK')"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_COMFYUI_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        r = subprocess.run(
            [launch_python, "-c", code],
            cwd=str(_COMFYUI_DIR),
            env=env,
            capture_output=True,
            timeout=_TIMEOUT_VERIFY,
            **({"creationflags": 0x08000000} if sys.platform == "win32" else {}),
        )
        out = (r.stdout or b"").decode("utf-8", errors="replace")
        err = (r.stderr or b"").decode("utf-8", errors="replace")
        tail = (err or out).strip().splitlines()[-6:]
        if r.returncode == 0 and "COMFY_IMPORT_OK" in out:
            return True, "OK"
        return False, "\n".join(tail) or f"exit={r.returncode}"
    except Exception as e:
        return False, str(e)


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
            "min_vram_mb": reg.get("min_vram_mb", 0),
            "recommended": reg.get("recommended", False),
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
                capture_output=True, timeout=5, **_TEXT_KW,
            )
            if r.returncode == 0:
                cpu_name = r.stdout.strip().split("\n")[0].strip()
        except Exception:
            pass
    else:
        try:
            r = subprocess.run(
                ["lscpu"], capture_output=True, timeout=3, **_TEXT_KW
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
            capture_output=True, timeout=5, **_TEXT_KW,
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
    """CUDA 版本。

    复用 _read_nvidia_smi_text()（安全解码）+ _parse_cuda_version()（正则提取），
    与组件检测里的 CUDA 解析保持同一套逻辑，避免两处表现不一致。
    """
    ver = _parse_cuda_version(_read_nvidia_smi_text())
    return {"cuda_version": None if ver == "unknown" else ver}


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
