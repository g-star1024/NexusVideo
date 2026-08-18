#!/usr/bin/env python3
"""
NexusVideo ComfyUI Worker 启动脚本
- 挂载 NAS 模型目录
- 启动 ComfyUI API Server（端口 9000）
- 启动健康检查 Flask 服务（端口 8000）
- 支持 --spot-mode 优雅中断处理

用法：
  python3 start_worker.py [--port 9000] [--spot-mode]
"""

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
import json
from pathlib import Path

# Flask 健康检查端点
try:
    from flask import Flask, jsonify
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

app = Flask(__name__)

# Worker 状态
WORKER_STATE = {
    "status": "initializing",
    "gpu_available": False,
    "nas_mounted": False,
    "comfyui_ready": False,
    "active_tasks": 0,
    "uptime_seconds": 0,
    "spot_mode": False,
}

# ComfyUI 进程
COMFYUI_PROCESS = None
SHUTDOWN_REQUESTED = False


def check_nas_mount():
    """检查 NAS 是否挂载到 /models"""
    models_dir = Path("/models")
    if models_dir.exists() and models_dir.is_mount():
        WORKER_STATE["nas_mounted"] = True
        # 统计模型数量
        model_files = list(models_dir.rglob("*.safetensors")) + list(models_dir.rglob("*.ckpt"))
        WORKER_STATE["model_count"] = len(model_files)
        return True
    return False


def check_gpu():
    """检查 GPU 可用性"""
    try:
        import torch
        if torch.cuda.is_available():
            WORKER_STATE["gpu_available"] = True
            WORKER_STATE["gpu_name"] = torch.cuda.get_device_name(0)
            WORKER_STATE["gpu_memory_gb"] = round(
                torch.cuda.get_device_properties(0).total_mem / (1024**3), 1
            )
            return True
    except Exception:
        pass
    return False


@app.route("/health")
def health():
    """Liveness probe — 仅检查进程存活"""
    WORKER_STATE["status"] = "running"
    return jsonify({"status": "alive"}), 200


@app.route("/ready")
def ready():
    """Readiness probe — 检查是否可以接收新任务"""
    all_ok = (
        WORKER_STATE["gpu_available"]
        and WORKER_STATE["nas_mounted"]
        and WORKER_STATE["comfyui_ready"]
        and WORKER_STATE["active_tasks"] < 3  # 并发上限
    )
    status = 200 if all_ok else 503
    return jsonify(WORKER_STATE), status


@app.route("/metrics")
def metrics():
    """Prometheus 指标端点（文本格式）"""
    lines = []
    lines.append(f'# HELP nexusvideo_worker_status Worker 状态（1=运行，0=停止）')
    lines.append(f'# TYPE nexusvideo_worker_status gauge')
    lines.append(f'nexusvideo_worker_status{{gpu="{WORKER_STATE.get("gpu_name","unknown")}"}} 1')
    lines.append(f'# HELP nexusvideo_worker_active_tasks 当前活动任务数')
    lines.append(f'# TYPE nexusvideo_worker_active_tasks gauge')
    lines.append(f'nexusvideo_worker_active_tasks {WORKER_STATE["active_tasks"]}')
    lines.append(f'# HELP nexusvideo_worker_uptime_seconds 运行秒数')
    lines.append(f'# TYPE nexusvideo_worker_uptime_seconds gauge')
    lines.append(f'nexusvideo_worker_uptime_seconds {WORKER_STATE["uptime_seconds"]}')
    lines.append(f'# HELP nexusvideo_worker_gpu_available GPU 可用（1=是，0=否）')
    lines.append(f'# TYPE nexusvideo_worker_gpu_available gauge')
    lines.append(f'nexusvideo_worker_gpu_available {int(WORKER_STATE["gpu_available"])}')
    lines.append(f'# HELP nexusvideo_worker_spot_mode 抢占式模式（1=是，0=否）')
    lines.append(f'# TYPE nexusvideo_worker_spot_mode gauge')
    lines.append(f'nexusvideo_worker_spot_mode {int(WORKER_STATE["spot_mode"])}')
    return "\n".join(lines), 200, {"Content-Type": "text/plain"}


def start_comfyui(port):
    """启动 ComfyUI API Server"""
    global COMFYUI_PROCESS, WORKER_STATE

    comfyui_dir = Path("/app/ComfyUI")
    if not comfyui_dir.exists():
        print("[ERROR] ComfyUI 目录不存在，跳过启动")
        return False

    cmd = [
        sys.executable,
        str(comfyui_dir / "main.py"),
        "--listen", "0.0.0.0",
        "--port", str(port),
        "--disable-metadata",
    ]

    print(f"[INFO] 启动 ComfyUI API: {' '.join(cmd)}")
    COMFYUI_PROCESS = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # 等待 ComfyUI 就绪
    for i in range(60):
        time.sleep(2)
        try:
            import urllib.request
            resp = urllib.request.urlopen(
                f"http://localhost:{port}/history",
                timeout=2
            )
            if resp.status == 200:
                WORKER_STATE["comfyui_ready"] = True
                print("[INFO] ComfyUI 已就绪")
                return True
        except Exception:
            continue

    print("[WARN] ComfyUI 启动超时，Worker 将以降级模式运行")
    return False


def uptime_loop():
    """后台线程：更新 uptime"""
    while not SHUTDOWN_REQUESTED:
        WORKER_STATE["uptime_seconds"] += 1
        time.sleep(1)


def graceful_shutdown(signum, frame):
    """优雅关闭（处理 SIGTERM / 抢占式回收）"""
    global SHUTDOWN_REQUESTED, COMFYUI_PROCESS
    print(f"\n[INFO] 收到信号 {signum}，开始优雅关闭...")
    SHUTDOWN_REQUESTED = True
    WORKER_STATE["status"] = "shutting_down"

    if COMFYUI_PROCESS and COMFYUI_PROCESS.poll() is None:
        print("[INFO] 等待 ComfyUI 完成当前任务（最长 120s）...")
        try:
            COMFYUI_PROCESS.wait(timeout=120)
        except subprocess.TimeoutExpired:
            print("[WARN] 超时，强制终止 ComfyUI")
            COMFYUI_PROCESS.kill()

    print("[INFO] 所有进程已停止，Worker 退出")
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="NexusVideo ComfyUI Worker")
    parser.add_argument("--port", type=int, default=9000, help="ComfyUI API 端口")
    parser.add_argument("--probe-port", type=int, default=8000, help="健康检查端口")
    parser.add_argument("--spot-mode", action="store_true", help="抢占式实例模式（加强排空逻辑）")
    args = parser.parse_args()

    WORKER_STATE["spot_mode"] = args.spot_mode

    # 注册信号处理
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)

    print("=" * 50)
    print("  NexusVideo ComfyUI Worker")
    print("=" * 50)
    print(f"  Port: {args.port}")
    print(f"  Probe Port: {args.probe_port}")
    print(f"  Spot Mode: {args.spot_mode}")
    print("=" * 50)

    # 初始化检查
    print("[INFO] 检查 GPU...")
    check_gpu()
    print("[INFO] 检查 NAS 挂载...")
    check_nas_mount()

    # 启动 ComfyUI
    start_comfyui(args.port)

    # 启动 uptime 线程
    threading.Thread(target=uptime_loop, daemon=True).start()

    # 启动健康检查服务
    if FLASK_AVAILABLE:
        print(f"[INFO] 健康检查端点启动于 http://0.0.0.0:{args.probe_port}")
        app.run(host="0.0.0.0", port=args.probe_port, debug=False, use_reloader=False)
    else:
        print("[ERROR] Flask 未安装，健康检查端点不可用")
        sys.exit(1)


if __name__ == "__main__":
    main()