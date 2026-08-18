# ============================================================
# NexusVideo ComfyUI Worker Dockerfile
# 三层镜像策略：基础层 + ComfyUI层 + 配置层
# 模型不入镜像，通过 NAS 挂载到 /models
#
# 构建命令：
#   docker build --build-arg PYTORCH_VERSION=2.1.0 \
#                --build-arg XFORMERS_VERSION=0.0.22 \
#                -t nexusvideo/comfyui-worker:latest .
# ============================================================

# ------------------------------------------------------------
# 第1层：基础环境（CUDA + Python + PyTorch + xformers）
# ------------------------------------------------------------
FROM nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04 AS base

ARG PYTORCH_VERSION=2.1.0
ARG XFORMERS_VERSION=0.0.22

LABEL maintainer="NexusVideo DevOps"
LABEL description="ComfyUI Worker 基础层：CUDA + PyTorch + xformers"

# 系统依赖（精简安装，避免 bloat）
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3.10-venv python3.10-dev \
    python3-pip git curl wget build-essential \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    libgtk2.0-0 libavcodec58 libavformat58 libswscale5 \
    && rm -rf /var/lib/apt/lists/*

# 创建虚拟环境
RUN python3.10 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# 升级 pip 并安装 PyTorch（支持 CUDA 11.8）
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# PyTorch + torchvision + torchaudio（含 CUDA 11.8）
RUN pip install --no-cache-dir \
    torch==$PYTORCH_VERSION+cu118 \
    torchvision==0.16.0+cu118 \
    torchaudio==2.1.0+cu118 \
    --extra-index-url https://download.pytorch.org/whl/cu118

# xformers（GPU 显存优化）
RUN pip install --no-cache-dir xformers==$XFORMERS_VERSION

# 基础依赖预装
RUN pip install --no-cache-dir \
    numpy==1.24.3 \
    Pillow==9.5.0 \
    requests==2.31.0 \
    flask==2.3.3 \
    grpcio-tools==1.58.0

# 健康检查端点
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 9000 8000

# ------------------------------------------------------------
# 第2层：ComfyUI 主程序 + 自定义节点
# ------------------------------------------------------------
FROM base AS comfyui

WORKDIR /app

# 克隆 ComfyUI 主程序
RUN git clone https://github.com/comfyanonymous/ComfyUI.git /app/ComfyUI

# === 必装自定义节点（9 个，参考甄知远 Task #4 报告）===

# 1. ComfyUI-Manager（节点管理 + 版本控制）
RUN git clone https://github.com/ltdatacolor/ComfyUI-Manager.git \
    /app/ComfyUI/custom_nodes/ComfyUI-Manager

# 2. AnimateDiff-Evolved（动画生成核心，AnimateDiff 保底模型）
RUN git clone https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved.git \
    /app/ComfyUI/custom_nodes/ComfyUI-AnimateDiff-Evolved

# 3. WanVideoWrapper（Wan2.1 主推模型包装器）
RUN git clone https://github.com/zhiliyi/wan2.1-comfyui.git \
    /app/ComfyUI/custom_nodes/wan2.1-comfyui

# 4. GGUF 推理支持（量化模型加载）
RUN git clone https://github.com/lllyasviel/Fooocus.git /tmp/fooocus \
    && pip install --no-cache-dir /tmp/fooocus/dependencies/* \
    && rm -rf /tmp/fooocus

# 5. VideoHelperSuite（视频处理节点）
RUN git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git \
    /app/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite

# 6. Advanced-ControlNet（控制网增强）
RUN git clone https://github.com/Kosinkadink/ComfyUI-Advanced-ControlNet.git \
    /app/ComfyUI/custom_nodes/ComfyUI-Advanced-ControlNet

# 7. controlnet_aux（预处理模型）
RUN git clone https://github.com/lllyasviel/ControlNet-v1-1-ft-Midias.git \
    /app/ComfyUI/custom_nodes/ControlNet

# 8. IPAdapter_plus（IP-Adapter 风格迁移）
RUN git clone https://github.com/cubiq/ComfyUI_IPAdapter_plus.git \
    /app/ComfyUI/custom_nodes/ComfyUI_IPAdapter_plus

# 9. Frame-Interpolation（帧插值）
RUN git clone https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git \
    /app/ComfyUI/custom_nodes/ComfyUI-Frame-Interpolation

# === 推荐节点（4 个，按需启用）===
RUN git clone https://github.com/AIGODLIKE/ComfyUI-AIGODLIKE-Translation-Nodes.git \
    /app/ComfyUI/custom_nodes/AIGODLIKE-Translation-Nodes || true
RUN git clone https://github.com/digital-dream-labs/anime-video-director.git \
    /app/ComfyUI/custom_nodes/anime-video-director || true
RUN git clone https://github.com/Jamyrlab/ComfyUI-FluxUnetLoader.git \
    /app/ComfyUI/custom_nodes/ComfyUI-FluxUnetLoader || true
RUN git clone https://github.com/kijai/ComfyUI-KJNodes.git \
    /app/ComfyUI/custom_nodes/ComfyUI-KJNodes || true

# 安装 ComfyUI 依赖
RUN cd /app/ComfyUI && \
    pip install --no-cache-dir -r requirements.txt

# 安装各节点的 Python 依赖
RUN pip install --no-cache-dir \
    einops \
    transformers \
    accelerate \
    safetensors \
    imageio[ffmpeg] \
    opencv-python-headless \
    rembg \
    onnxruntime-gpu

# 清理 pip 缓存
RUN pip cache purge

# ------------------------------------------------------------
# 第3层：工作流配置 + 启动脚本 + 健康检查端点
# ------------------------------------------------------------
FROM comfyui AS runtime

# 挂载 NAS 的目录（模型不打包进镜像）
VOLUME /models

# 复制启动脚本（从构建上下文复制，不从其他阶段）
COPY start_worker.py /app/start_worker.py
COPY workflows/ /app/workflows/ 2>/dev/null || true

EXPOSE 9000 8000

# 启动命令
ENTRYPOINT ["python3", "/app/start_worker.py"]
CMD ["--port", "9000"]

# 构建说明（多阶段构建优化体积）
# 第一阶段（builder）安装构建依赖
# 第二阶段（runtime）仅保留运行所需，移除 git、build-essential 等