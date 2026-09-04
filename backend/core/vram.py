"""
NexusVideo Backend - 显存探测工具
============================================================
提供与本机 NVIDIA 显卡显存相关的轻量探测函数。

设计要点：
  - 只依赖标准库（subprocess / shutil），不引入任何业务模块，
    以便 backend/core/process_manager.py（拼装 ComfyUI 启动参数）
    与 backend/routers/settings.py（模型列表显存过滤）都能安全导入，
    避免循环依赖。
  - 所有函数失败时返回 None / False（显存信息缺失时由 caller 决定降级行为）。
  - 单次 nvidia-smi 调用，开销极低，可在组件检测与进程启动路径中频繁调用。
"""

import shutil
import subprocess

# 中文 Windows 兼容：nvidia-smi 输出可能含非 UTF-8 字节（GBK 中文），
# subprocess.run(text=True) 会严格解码失败并把 stdout 置为 None，
# 导致显存探测静默返回 None（表现为"显存未知 / 模型全部警告爆显存"）。
# 统一用 errors="replace" 保证 stdout 一定是 str。详见 routers/settings.py 同名常量。
_TEXT_KW = {"encoding": "utf-8", "errors": "replace"}


def _has_nvidia_gpu() -> bool:
    """轻量判断是否存在 NVIDIA 显卡（只看 nvidia-smi 是否可执行）。"""
    return shutil.which("nvidia-smi") is not None


def _get_vram_total_mb() -> "int | None":
    """
    读取本机第一块 NVIDIA 显卡的总显存（MB）。

    无 GPU / nvidia-smi 不可用 / 解析失败 → 返回 None。
    供模型显存过滤与 ComfyUI 启动参数（--lowvram/--medvram）拼装共用。
    """
    if not _has_nvidia_gpu():
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, timeout=5, **_TEXT_KW,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        first = result.stdout.strip().split("\n")[0].strip()
        return int(first) if first.isdigit() else None
    except Exception:
        return None


def _get_vram_free_mb() -> "int | None":
    """
    读取本机第一块 NVIDIA 显卡的当前空闲显存（MB）。

    无 GPU / 解析失败 → 返回 None。
    """
    if not _has_nvidia_gpu():
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, timeout=5, **_TEXT_KW,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        first = result.stdout.strip().split("\n")[0].strip()
        return int(first) if first.isdigit() else None
    except Exception:
        return None
