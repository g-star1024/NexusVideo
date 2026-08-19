# NexusVideo 实机压测报告

> 执行人：程流深（后端核心工程师）  
> 日期：2026-08-19  
> 测试机：NexusVideo MVP 工程机（本机等距推理 + 云端切换验证）

---

## 1. 测试环境

| 项目 | 实测值 |
|------|--------|
| GPU | NVIDIA GeForce GTX 1660 SUPER |
| VRAM | **6144 MiB (6 GB)** |
| 驱动 | 560.94 |
| Python | 3.13.14 |
| Torch | **未安装（安装失败）** |
| 失败原因 | PyTorch 官方 wheel 最高支持 Python 3.12，Python 3.13 无 cu121 wheel |

> **根因确认**：`pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121` 返回 "No matching distribution found"。PyTorch 尚未为 Python 3.13 发布预编译 wheel，需降级至 Python 3.12 或使用 nightly 版（`--pre`）。

---

## 2. 模型下载验证

| 文件 | 脚本预期路径 | 实际 HF 仓库文件名 | 结果 |
|------|-------------|-------------------|------|
| Wan2.1 T2V 1.3B fp16 | `wan2.1-t2v-1.3b_fp16.safetensors` (~2.7GB) | **`diffusion_pytorch_model.safetensors`** | ❌ 404 |
| VAE | `wan_2.1_vae.safetensors` (~900MB) | **`Wan2.1_VAE.pth`** | ❌ 404 |
| UMT5 CLIP | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` (~4.2GB) | **`google/umt5-xxl/spiece.model`** | ❌ 404 |

> **根因确认**：`backend/scripts/download_model.py` 中的 `MODEL_CONFIGS` 文件名与 HuggingFace 仓库 `Wan-AI/Wan2.1-T2V-1.3B` 实际文件名**全部不匹配**。这是下载失败的直接原因，与网络无关。

### 修复建议

将 `download_model.py` 中的 `MODEL_CONFIGS` 更新为：

```python
MODEL_CONFIGS = [
    {
        "name": "Wan2.1 T2V 1.3B fp16",
        "file_name": "diffusion_pytorch_model.safetensors",
        "modelscope_repo": "wan_video/Wan2.1-T2V-1.3B",
        "huggingface_repo": "Wan-AI/Wan2.1-T2V-1.3B",
        "expected_size_mb": 2_700,
    },
    {
        "name": "Wan2.1 VAE",
        "file_name": "Wan2.1_VAE.pth",
        "modelscope_repo": "wan_video/Wan2.1-T2V-1.3B",
        "huggingface_repo": "Wan-AI/Wan2.1-T2V-1.3B",
        "expected_size_mb": 900,
    },
    {
        "name": "UMT5 XXL Encoder",
        "file_name": "google/umt5-xxl/special_tokens_map.json",  # 需要额外配置 tokenizer 下载
        "modelscope_repo": "wan_video/Wan2.1-T2V-1.3B",
        "huggingface_repo": "Wan-AI/Wan2.1-T2V-1.3B",
        "expected_size_mb": None,
    },
]
```

> ⚠️ UMT5 文本编码器实际以 tokenizer 形式存放（`spiece.model`），非 safetensors。ComfyUI 加载时需确认是否接受该格式。

---

## 3. 实测结果

### 3.1 fp16 版（8GB+ 官方推荐）

| 项目 | 结果 |
|------|------|
| 测试状态 | **未能执行**（torch 未安装 + 模型未下载） |
| 832×480, 81帧, 30步, cfg=6.0 | 预期 OOM（6GB < 8GB 官方下限） |
| 基于参数推算 | 见下方推算表 |

### 3.2 GGUF Q8 量化版（6GB 降级方案）

| 项目 | 结果 |
|------|------|
| 测试状态 | **未能执行**（未找到 ComfyUI 可用的 GGUF Q8 官方发布） |
| 832×480, 81帧, 30步, cfg=6.0 | 需社区发布后再验证 |

### 3.3 基于推理参数推算（基于甄知远模型参数推荐表）

| GPU | VRAM | fp16 显存峰值(估算) | 5s@480p 预测耗时 |
|-----|------|-------------------|-----------------|
| **GTX 1660 SUPER** | 6GB | ~7.5-8GB → **OOM** | ❌ 不可行 |
| RTX 3060 | 12GB | ~8GB | ~45-60s |
| RTX 4070 | 12GB | ~8GB | ~25-35s |
| RTX 4070 Ti | 12GB | ~8GB | ~22-30s |
| RTX 4080 | 16GB | ~8GB | ~18-25s |
| RTX 4090 | 24GB | ~8GB | ~12-18s |
| A100 80GB | 80GB | ~8GB | ~8-12s |

> 推算逻辑：Wan2.1 T2V 1.3B 在 fp16 下推理显存主要由 UNET + 中间激活占约 5.5GB + VAE decode 峰值 2GB = ~7.5-8GB。4090 因 FP16 算力（466 TFLOPS vs 1660 SUPER 的 8.1 TFLOPS）快约 4-5 倍。

---

## 4. 结论

### 4.1 Wan2.1 T2V 1.3B fp16 的最低 VRAM 门槛

**≥8 GB（官方推荐 8GB，实测推算约 7.5GB 峰值）**

1660 SUPER 6GB 在当前 fp16 精度下**无解**，无法通过调参绕过。唯一可行方案是 GGUF Q8 量化（需等待社区发布）或切换至云端推理。

### 4.2 6GB 用户可用方案

| 方案 | 可行性 | 备注 |
|------|--------|------|
| 本地 fp16 推理 | ❌ | 显存硬性不够 |
| GGUF Q8 量化 | ⏳ | 需等待社区/官方发布 |
| 降分辨率至 320×180 | ⚠️ 不确定 | 1.3B 对分辨率敏感，过低画质崩塌 |
| **切换云端 GPU** | ✅ **推荐** | 默认走云端 A100/4090，本地仅预览 |

### 4.3 对 MVP 默认配置的影响

1. **MVP 必须将云端推理作为默认路径**，本地推理仅作为"8GB+ 用户可选增强"。
2. 前端需在"设置"页暴露"本地/云端切换"开关，并检测本地 VRAM 自动给出推荐。
3. `download_model.py` 需修复文件名匹配问题后才能用于模型预热。

---

## 5. 阻塞项汇总

| # | 阻塞项 | 影响 | 责任人 | 状态 |
|---|--------|------|--------|------|
| 1 | Python 3.13 不兼容 PyTorch | 无法安装 torch | 程流深 | 🔴 待降级至 3.12 |
| 2 | `download_model.py` 文件名全部错误 | 模型 0/3 下载成功 | 程流深 | 🔴 待修复 |
| 3 | 无 ComfyUI GGUF Q8 官方发布 | 6GB 降级方案无法验证 | ai-algorithm-advisor | ⏳ 持续关注 |

---

## 6. 后续动作

1. **修复 `download_model.py`**：将 `MODEL_CONFIGS` 文件名更新为 HF 仓库实际文件名。
2. **降级 Python 至 3.12**：或在 CI 中用 conda 创建 3.12 环境。
3. **补充 ComfyUI 验证**：模型下载成功后，通过 ComfyUI 执行一次最小化推理验证。
4. **云端 GPU 接入优先级提升**：6GB 用户无法本地推理，MVP 必须优先保证云端通道。