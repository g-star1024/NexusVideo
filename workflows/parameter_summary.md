# NexusVideo 参数推荐汇总表

> 作者：程流深（python-backend-core）
> 基于：甄知远 Task #4 模型选型报告
> 日期：2026-08-18

---

## 一、三大模式 × 四档显存参数推荐矩阵

### 模式一：文生视频（T2V）

| 参数 | 6GB 保底 | 8GB 默认（MVP） | 12GB 高清 | 24GB 旗舰 |
|------|---------|---------------|----------|----------|
| **模型** | AnimateDiff (SD1.5) | **Wan2.1 14B GGUF Q4** | Wan2.1 14B GGUF Q6 | Wan2.1 14B bf16 满血 |
| **分辨率** | 512×512 | **832×480** | 1280×720 | 1280×720 |
| **帧数** | 17 | **25** | 25 | 49 |
| **fps (原始)** | 8 | **8** | 8 | 16 |
| **RIFE 插帧后** | 24fps | **24fps** | 24fps | 24fps |
| **实际时长** | ~2秒 | **~3秒** | ~3秒 | ~3秒 |
| **steps** | 20 | **25** | 25 | 25 |
| **cfg** | 7.0 | **5.0** | 5.0 | 5.0 |
| **sampler** | euler_ancestral | **euler** | euler | euler |
| **scheduler** | normal | **normal** | normal | normal |
| **精度** | fp16 | **GGUF Q4** | GGUF Q6 | bf16 |
| **预期时间** | 3-6min | **2-4min** | 1.5-3min | 1-2min |
| **优化手段** | xformers + Tiled VAE + CPU offload | fp16 + Tiled VAE + TeaCache | fp8 + TeaCache + SageAttn | bf16 + SageAttn + fullgraph |

### 模式二：图生视频（I2V）

| 参数 | 6GB 保底 | 8GB 默认（MVP） | 12GB 高清 | 24GB 旗舰 |
|------|---------|---------------|----------|----------|
| **模型** | AnimateDiff (SD1.5) | **AnimateDiff + CN** / Wan-I2V Q4 | Wan-I2V Q6 | Wan-I2V bf16 |
| **ControlNet** | Tile@0.5 | **Tile@0.6** | Tile@0.6 | IP-Adapter@0.65 |
| **分辨率** | 512×512 | **512×512** | 768×512 | 1280×720 |
| **帧数** | 16 | **25** | 25 | 49 |
| **fps** | 8→24fps(插帧) | **8→24fps(插帧)** | 8→24fps | 16→24fps |
| **steps** | 20 | **20** | 20 | 20 |
| **cfg** | 7.0 | **7.0** | 7.0 | 7.0 |
| **denoise(默认)** | 0.55 | **0.55** | 0.55 | 0.55 |
| **motion_module** | mm_sd_v15_v2 | **mm_sd_v15_v2** | mm_sd_v15_v2 | mm_sd_v15_v2 |
| **预期时间** | 4-8min | **3-5min** | 2-4min | 1.5-3min |

**运动强度滑块映射（所有显存档通用）：**

| motion_strength | denoise | 效果 |
|----------------|---------|------|
| 1 | 0.36 | 微动（发丝飘动、水面涟漪） |
| 3 | 0.48 | 轻微运动 |
| **5（默认）** | **0.60** | **明显运动（通用主力档）** |
| 7 | 0.72 | 较剧烈运动 |
| 10 | 0.90（硬上限） | 剧烈运动（可能偏离原图） |

### 模式三：视频风格化（V2V）— MVP 轻量版

| 参数 | 6GB 保底 | 8GB 默认（MVP） | 12GB 高清 | 24GB 旗舰 |
|------|---------|---------------|----------|----------|
| **模型** | AnimateDiff (SD1.5) | **AnimateDiff + CN Depth** | 同左 | 同左 + 多CN(V2) |
| **ControlNet** | Depth@0.70 | **Depth@0.75** | Depth@0.75 | Depth+CN(V2) |
| **分辨率** | 512×512 | **512×512** | 512×512 | 768×768 |
| **帧数** | 16 | **25** | 30 | 49 |
| **fps** | 8→24fps | **8→24fps** | 8→24fps | 8→24fps |
| **steps** | 20 | **25** | 25 | 25 |
| **风格预设** | 油画/3D/水墨 | **油画/3D/水墨** | 同左 | 同左 + 扩展 |

**3 套风格预设参数：**

| 风格 | denoise | CFG | CN Strength | 正向 prompt 特征 |
|------|---------|-----|-------------|-----------------|
| **油画 (oil)** | 0.55 | 7.0 | 0.75 | oil painting, thick brushstrokes, baroque lighting |
| **3D (3d)** | 0.60 | 7.5 | 0.75 | 3d pixar, octane render, vibrant colors |
| **水墨 (ink)** | 0.55 | 6.5 | 0.75 | chinese ink wash, sumi-e, minimalistic |

---

## 二、显存降级链（OOM 自动降级策略）

当 ComfyUI 返回 OOM 错误时，`workflow_translator.apply_degradation()` 按以下阶梯自动降级：

```
OOM 触发
  │
  ├─ Level 1: 降分辨率（width/height × 0.75，最低 256）
  │
  ├─ Level 2: 降帧数（frames × 0.5，最少 8 帧）
  │
  ├─ Level 3: 降步数（steps - 5，最少 10 步）
  │
  ├─ Level 4: 降 CFG（cfg - 2.0，最低 4.0）
  │
  └─ 仍失败 → error_code=11004，提示"显存不足，建议切换云端"
```

### 降级链对应的 ComfyUI 报错关键字检测

`task_manager._is_oom_error()` 检测以下关键字：
- `"out of memory"`
- `"CUDA out of memory"`
- `"OutOfMemoryError"`
- `" HIP out of memory"` (AMD GPU)
- `"Tried to allocate"`

### 降级阶梯与模型参数的对应关系

| 原始参数 (8GB Wan2.1) | L1 | L2 | L3 | L4 |
|----------------------|-----|-----|-----|-----|
| 832×480 | **624×360** | 624×360 | 624×360 | 624×360 |
| 25 帧 | 25 | **13** | 13 | 13 |
| 25 步 | 25 | 25 | **20** | 20 |
| cfg 5.0 | 5.0 | 5.0 | 5.0 | **3.0** |

最大降级次数：`settings.max_retry = 2`，即最多执行 Level 2 降级。

---

## 三、报错根因诊断表

| 现象 | 可能原因 | 处置 |
|------|---------|------|
| **`节点连接错误 / node_id not found`** | 工作流 JSON 中引用了不存在的节点 ID | 检查工作流模板的节点连线，确保输入输出节点 ID 一致 |
| **`Unknown class_type: XXX`** | 缺少自定义节点或节点名拼写错误 | 检查 9 个必装节点是否已安装，节点类名与工作流模板一致 |
| **`CUDA out of memory`** | 显存不足 OOM | 自动降级链处理（见上表），或手动调低分辨率/帧数/步数 |
| **`Failed to load model`** | 模型文件不存在或损坏 | 检查模型文件路径，重新下载模型 |
| **`Tried to allocate X bytes`** | 显存碎片化或峰值超出 | 尝试重启 ComfyUI，或降级参数 |
| **`节点执行超时`** | 模型过大导致加载超时 | 检查模型文件是否完整，降低并发任务数 |
| **`JSON 解析失败`** | 工作流模板格式错误 | 验证 JSON 合法性，检查占位符是否完整替换 |
| **`Connection refused`** | ComfyUI 未运行或端口不对 | 检查 ComfyUI 进程状态和端口配置 |
| **`WebSocket 断连`** | ComfyUI 重启或崩溃 | 进程管理器自动重启 ComfyUI，前端重新连接 |
| **`视频文件输出为空`** | FFmpeg 编码器配置错误 | 检查 FFMPEG_VideoCombine 节点参数，确保 libx264 可用 |

---

## 四、WebSocket 进度事件映射

ComfyUI WebSocket 事件 → 前端文案化进度映射表：

| ComfyUI 事件 | 含义 | 前端进度 | 文案 |
|-------------|------|---------|------|
| `progress` (start) | 任务开始执行 | 0-5% | "正在理解你的创意..." |
| `progress` (step N/total) | 采样步进度 | 5-80% | 步数线性映射 |
| `executing` (node_id=X) | 开始执行节点 X | 根据节点位置映射 | |
| `executed` (node_id=X) | 节点 X 完成 | 节点完成百分比 | |
| `queue_error` | 排队失败 | — | "任务提交失败，请重试" |
| `executed` (Final: FFMPEG) | FFmpeg 合成完成 | 90-95% | "正在合成视频..." |
| `progress` (100%) | 全部完成 | 100% | "生成完成！" |

### 进度百分比计算逻辑

```
总节点数 N = len(workflow)
已执行节点数 n
总采样步数 S
当前步数 s

progress = (n / N) × 0.85 + (s / S) × 0.10 + 0.05
```

---

## 五、模型版本与缓存策略

### 模型文件清单与路径

| 模型文件 | 用途 | 路径 | 体积 |
|---------|------|------|------|
| v1-5-pruned-emaonly.fp16.safetensors | AnimateDiff 底座 | models/checkpoints/ | ~2GB |
| mm_sd_v15_v2.ckpt | AnimateDiff 运动模块 | models/animatediff_models/ | ~1.5GB |
| control_depth_fp16.safetensors | Depth ControlNet | models/controlnet/ | ~1.4GB |
| wan2.1-t2v-14b-Q4_K_M.gguf | Wan2.1 T2V 量化 | models/diffusion_models/ | ~8GB |
| wan2.1-i2v-14b-480p-Q5_K_M.gguf | Wan2.1 I2V 量化 | models/diffusion_models/ | ~8GB |
| umt5_xxl_fp8_e4m3fn_scaled.safetensors | Wan 文本编码器 | models/text_encoders/ | ~5GB |
| wan_2.1_vae.safetensors | Wan VAE | models/vae/ | ~0.5GB |
| rife49.pth | RIFE 插帧模型 | models/frame_interpolation/ | ~0.1GB |

### 缓存策略

- **工作流模板缓存**：`WorkflowTranslator` 类内部缓存，首次加载后从内存读取
- **模型文件缓存**：ComfyUI 首次加载时缓存到 GPU 显存，同一模型后续推理直接复用
- **VAE 缓存**：`VAELoader` 加载后由 ComfyUI 内部管理
- **缓存清理**：`translator.clear_cache()` 可手动触发模板缓存清空

---

## 六、FFmpeg 视频合成命令

工作流中通过 `FFMPEG_VideoCombine` 节点完成视频合成，关键参数：

```
- 帧率: 24fps（RIFE 插帧后）
- 编码器: libx264
- CRF: 18（高质量）
- 输出格式: video/mp4
- 文件名前缀: nexus_t2v / nexus_v2v
- 保留元数据: true
```

对应的命令行等价物（调试用）：
```bash
ffmpeg -framerate 24 -i %04d.png -c:v libx264 -crf 18 -movflags +faststart output.mp4
```

---

## 七、与 FastAPI 服务对接要点

1. **`workflow_translator.py`** 负责模板加载 + 占位符替换 + 字段映射
2. **`routers/generate.py`** 负责参数校验 + 任务提交
3. **`task_manager.py`** 负责后台跟踪 + OOM 降级重试
4. **运动强度映射** `motion_strength_to_denoising()` 在 `workflow_translator.py` 中实现
5. **风格预设拼装** `_process_video2video()` 在 `workflow_translator.py` 中实现
6. **模板路径**：项目根目录 `workflows/` 下，`config.py` 中 `workflows_dir` 已配置