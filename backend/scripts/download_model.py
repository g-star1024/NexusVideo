"""
NexusVideo - 模型下载脚本
============================================================
从 HuggingFace / ModelScope 下载 Wan2.1 T2V 1.3B 模型套件，
包含主模型（fp16）、VAE（pth）、UMT5-XXL 文本编码器（bf16）。
支持断点续传。优先 HuggingFace，不可用时 fallback 到 ModelScope。

⚠ 三模型合计约 17.6 GB（5.68 + 0.5 + 11.4），请确保磁盘空间充足。

文件清单（HuggingFace: Wan-AI/Wan2.1-T2V-1.3B）:
    diffusion_pytorch_model.safetensors   # 5.68 GB  主模型 (fp16)
    Wan2.1_VAE.pth                        # 508 MB   VAE
    models_t5_umt5-xxl-enc-bf16.pth      # 11.4 GB  UMT5-XXL 文本编码器 (bf16)

依赖安装（如未安装）：
    pip install modelscope huggingface-hub tqdm requests

用法：
    python backend/scripts/download_model.py
    python backend/scripts/download_model.py --target-dir ./models
    python backend/scripts/download_model.py --force
"""

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

import requests
import tqdm


# ================================================================
# 模型元信息
# ================================================================
MODEL_CONFIGS = [
    {
        "name": "Wan2.1 T2V 1.3B fp16 主模型",
        "file_name": "diffusion_pytorch_model.safetensors",
        "modelscope_repo": "Wan-AI/Wan2.1-T2V-1.3B",
        "huggingface_repo": "Wan-AI/Wan2.1-T2V-1.3B",
        "expected_size_mb": 5_817,  # ~5.68 GB (fp16)
    },
    {
        "name": "Wan2.1 VAE",
        "file_name": "Wan2.1_VAE.pth",
        "modelscope_repo": "Wan-AI/Wan2.1-T2V-1.3B",
        "huggingface_repo": "Wan-AI/Wan2.1-T2V-1.3B",
        "expected_size_mb": 508,  # ~508 MB (pth)
    },
    {
        "name": "UMT5-XXL 文本编码器 bf16",
        "file_name": "models_t5_umt5-xxl-enc-bf16.pth",
        "modelscope_repo": "Wan-AI/Wan2.1-T2V-1.3B",
        "huggingface_repo": "Wan-AI/Wan2.1-T2V-1.3B",
        "expected_size_mb": 11_674,  # ~11.4 GB (bf16)
    },
]


def fmt_size(num_bytes: int) -> str:
    """格式化字节数为人类可读字符串。"""
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def download_file_with_resume(
    url: str,
    dest: Path,
    expected_size_mb: int | None = None,
    chunk_size: int = 8_192,  # 8KB chunks
) -> Path:
    """
    下载文件，支持断点续传。

    使用 HTTP Range 请求实现续传。
    如果目标文件已存在且大小合理，跳过下载。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # 检查是否已下载完成
    if dest.exists():
        actual_mb = dest.stat().st_size / (1024 * 1024)
        if expected_size_mb and actual_mb >= expected_size_mb * 0.95:
            print(f"  [SKIP] {dest.name} 已存在 ({fmt_size(dest.stat().st_size)})")
            return dest
        elif expected_size_mb and actual_mb < expected_size_mb * 0.5:
            print(f"  [WARN] {dest.name} 不完整 ({fmt_size(dest.stat().st_size)} / {expected_size_mb}MB)，重新下载")
            dest.unlink()

    # 获取文件大小
    print(f"  [INFO] 正在查询文件大小: {dest.name}")
    head_resp = requests.head(url, allow_redirects=True, timeout=30)
    total_size = int(head_resp.headers.get("Content-Length", 0))
    if total_size == 0:
        # 部分 CDN 不支持 HEAD，用 GET 获取
        print(f"  [WARN] HEAD 请求未返回 Content-Length，使用 GET 获取")
        resp = requests.get(url, stream=True, timeout=60)
        total_size = int(resp.headers.get("Content-Length", 0))
        if total_size == 0:
            print(f"  [ERROR] 无法获取文件大小，尝试直接下载...")

    print(f"  [INFO] {dest.name} 大小: {fmt_size(total_size)}")

    # 支持 Range 请求的断点续传
    start_pos = 0
    mode = "wb"
    if dest.exists():
        start_pos = dest.stat().st_size
        mode = "ab"
        print(f"  [INFO] 检测到已有 {fmt_size(start_pos)}，断点续传...")

    headers = {}
    if start_pos > 0 and start_pos < total_size:
        headers["Range"] = f"bytes={start_pos}-"

    with requests.get(url, stream=True, headers=headers, timeout=120) as resp:
        resp.raise_for_status()

        # 如果服务器不支持 Range，从头开始
        if start_pos > 0 and "Content-Range" not in resp.headers:
            print(f"  [WARN] 服务器不支持 Range 请求，从头重新下载")
            start_pos = 0
            mode = "wb"
            if dest.exists():
                dest.unlink()

        actual_size = int(resp.headers.get("Content-Length", total_size - start_pos))
        downloaded = start_pos

        with tqdm.tqdm(
            total=actual_size,
            initial=0,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=dest.name,
        ) as pbar:
            with open(dest, mode) as f:
                for chunk in resp.iter_content(chunk_size=chunk_size * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        pbar.update(len(chunk))

    # 验证
    final_size = dest.stat().st_size
    if expected_size_mb and final_size / (1024 * 1024) < expected_size_mb * 0.9:
        print(f"  [WARN] 下载完成但大小异常 ({fmt_size(final_size)} / {expected_size_mb}MB)")
        return dest

    print(f"  [DONE] {dest.name} -> {fmt_size(final_size)}")
    return dest


def download_from_modelscope(config: dict, target_dir: Path) -> bool:
    """
    尝试从 ModelScope 下载模型。

    使用 modelscope.snapshot_download 或直接 HTTP 下载。
    返回 True 表示成功。
    """
    print(f"\n{'='*60}")
    print(f"[ModelScope] 下载 {config['name']}")
    print(f"  仓库: {config['modelscope_repo']}")

    # 目标路径
    dest = target_dir / config["file_name"]

    # 优先尝试 modelscope SDK
    try:
        from modelscope.snapshot import snapshot_download  # type: ignore

        print(f"  [INFO] 使用 ModelScope SDK 下载...")
        cache_dir = snapshot_download(
            model_id=config["modelscope_repo"],
            cache_dir=str(target_dir),
        )

        # 找到下载的文件
        cache_path = Path(cache_dir)
        matches = list(cache_path.rglob(config["file_name"]))
        if matches:
            src = matches[0]
            # 拷贝到目标目录
            if src != dest:
                shutil.copy2(src, dest)
            print(f"  [DONE] {config['file_name']} 下载成功")
            return True
        else:
            print(f"  [WARN] ModelScope SDK 下载成功但未找到 {config['file_name']}")
            # fallback 到 HTTP
            return _try_http_download(config, target_dir)
    except ImportError:
        print(f"  [INFO] modelscope 未安装，使用 HTTP 下载...")
        return _try_http_download(config, target_dir)
    except Exception as e:
        print(f"  [ERROR] ModelScope SDK 失败: {e}")
        return _try_http_download(config, target_dir)


def _try_http_download(config: dict, target_dir: Path) -> bool:
    """尝试通过 HTTP 从 ModelScope 直接下载。"""
    base_url = f"https://modelscope.cn/api/v1/models/{config['modelscope_repo']}/revision/main"
    dest = target_dir / config["file_name"]

    try:
        # 尝试获取文件列表
        print(f"  [INFO] 尝试从 ModelScope HTTP API 获取文件...")
        resp = requests.get(base_url + "/files", timeout=30)
        resp.raise_for_status()
        files_data = resp.json()

        # 查找匹配的文件
        target_url = None
        for item in files_data.get("files", []):
            path = item.get("path", "")
            if config["file_name"] in path:
                target_url = item.get("url") or item.get("download_url")
                break

        if not target_url:
            print(f"  [WARN] ModelScope API 未找到文件 {config['file_name']}")
            return False

        download_file_with_resume(
            target_url,
            dest,
            expected_size_mb=config["expected_size_mb"],
        )
        return True

    except Exception as e:
        print(f"  [ERROR] ModelScope HTTP 下载失败: {e}")
        return False


def download_from_huggingface(config: dict, target_dir: Path) -> bool:
    """尝试从 HuggingFace 下载模型。"""
    print(f"\n{'='*60}")
    print(f"[HuggingFace] 下载 {config['name']}")
    print(f"  仓库: {config['huggingface_repo']}")

    dest = target_dir / config["file_name"]

    # 优先尝试 huggingface_hub
    try:
        from huggingface_hub import hf_hub_download  # type: ignore

        print(f"  [INFO] 使用 huggingface_hub 下载...")
        downloaded_path = hf_hub_download(
            repo_id=config["huggingface_repo"],
            filename=config["file_name"],
            local_dir=str(target_dir),
            local_dir_use_symlinks=False,
            resume_download=True,  # 支持断点续传
        )
        print(f"  [DONE] {config['file_name']} 下载成功")
        return True
    except ImportError:
        print(f"  [INFO] huggingface-hub 未安装，使用 HTTP 下载...")
    except Exception as e:
        print(f"  [WARN] huggingface_hub 下载失败: {e}，尝试 HTTP...")

    # HTTP fallback
    try:
        url = f"https://huggingface.co/{config['huggingface_repo']}/resolve/main/{config['file_name']}"
        print(f"  [INFO] HTTP URL: {url}")
        download_file_with_resume(
            url,
            dest,
            expected_size_mb=config["expected_size_mb"],
        )
        return True
    except Exception as e:
        print(f"  [ERROR] HuggingFace HTTP 下载失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="NexusVideo 模型下载脚本（ModelScope / HuggingFace）"
    )
    parser.add_argument(
        "--target-dir",
        type=str,
        default=None,
        help="模型存储目录（默认: {项目根}/comfyui/models 或 ~/.cache/nexusvideo/models）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新下载（忽略已存在文件）",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="只下载指定模型文件（如 wan2.1-t2v-1.3b_fp16.safetensors）",
    )
    args = parser.parse_args()

    # 确定目标目录
    if args.target_dir:
        target_dir = Path(args.target_dir)
    else:
        # 默认：ComfyUI 模型目录
        comfyui_models = Path("comfyui/models")
        if comfyui_models.exists():
            target_dir = comfyui_models / "unet"
        else:
            target_dir = Path.home() / ".cache" / "nexusvideo" / "models"

    target_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n{'#'*60}")
    print(f"# NexusVideo 模型下载")
    print(f"# 目标目录: {target_dir}")
    print(f"{'#'*60}")

    # 需要下载的模型列表
    to_download = []
    for config in MODEL_CONFIGS:
        if args.only and args.only != config["file_name"]:
            continue
        to_download.append(config)

    if not to_download:
        print(f"\n[ERROR] 未找到匹配的文件名: {args.only}")
        sys.exit(1)

    # 下载每个模型
    success_count = 0
    for config in to_download:
        print(f"\n{'*'*60}")
        print(f"下载模型 {MODEL_CONFIGS.index(config) + 1}/{len(to_download)}: {config['name']}")
        print(f"{'*'*60}")

        success = False

        # Step 1: 尝试 ModelScope
        try:
            success = download_from_modelscope(config, target_dir)
        except Exception as e:
            print(f"  [FATAL] ModelScope 下载异常: {e}")
            success = False

        # Step 2: ModelScope 失败 → fallback 到 HuggingFace
        if not success:
            print(f"\n  [FALLBACK] ModelScope 不可用，尝试 HuggingFace...")
            try:
                success = download_from_huggingface(config, target_dir)
            except Exception as e:
                print(f"  [FATAL] HuggingFace 下载异常: {e}")
                success = False

        if success:
            success_count += 1
            dest = target_dir / config["file_name"]
            if dest.exists():
                size = dest.stat().st_size
                print(f"\n  >>> 模型路径: {dest}")
                print(f"  >>> 文件大小: {fmt_size(size)}")
            else:
                print(f"\n  >>> [WARN] 下载报告成功但文件不存在")
        else:
            print(f"\n  >>> [FAIL] {config['name']} 下载失败")

    # 总结
    print(f"\n{'='*60}")
    print(f"下载完成: {success_count}/{len(to_download)} 成功")
    print(f"模型目录: {target_dir}")
    print(f"{'='*60}")

    if success_count < len(to_download):
        sys.exit(1)


if __name__ == "__main__":
    main()