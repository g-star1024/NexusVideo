# NexusVideo 模型选型与 ComfyUI 节点组合可行性评估报告

> 评估人：甄知远（AI 算法顾问）
> 日期：2026-08-18
> 对应任务：Task #4（P0-基建）
> 下游依赖：Task #7（三大模式 ComfyUI 工作流 JSON 模板编写与验证）

---

## 〇、核心结论（TL;DR，给主理人和工程团队先看）

1. **文生视频（T2V）主推模型升级为 Wan2.1（万相）**，而非白皮书原始设定的 AnimateDiff。原因：Wan2.1 于 2025 年 2 月开源，VBench 得分 86.22% 超越 Sora（84.28%），Apache 2.0 许可，且通过 GGUF 量化可在 6-8GB 显存消费级显卡上运行——这是白皮书定稿时尚未发布的模型，现在已成为开源视频生成的事实标杆。AnimateDiff 退居为 6GB 极低端设备的保底方案。

2. **图生视频（I2V）保留 AnimateDiff 作为低端保底，同时引入 Wan2.1-I2V GGUF 作为 8GB+ 设备的质量升级路径**。AnimateDiff + ControlNet(Tile) / IP-Adapter 组合在白皮书中已验证可行，社区成熟度高，6GB 可跑，MVP 阶段维持此方案风险最低。

3. **视频风格化（V2V）建议 MVP 仅做"轻量版"（单 ControlNet Depth + AnimateDiff，512×512），完整多 ControlNet 风格预设推迟到 V2**。原因：多 ControlNet 堆叠需 12-16GB+ 显存，与"小白 3 步出片"的极简目标和 6-8GB 目标机型冲突，工程复杂度也偏高。

4. **安装包体积策略改为"分层下载"**：随包内置运行时 + AnimateDiff 基线（控在 15-20GB 内满足白皮书），Wan2.1 大模型作为首次启动可选下载。避免单一安装包膨胀到 30GB+。

5. **自定义节点清单从白皮书的 3 个扩展到 9 个必装 + 4 个推荐**，新增 GGUF 加载器、视频 IO 套件、帧插值等——这些是 2025 年视频工作流的"水电煤"，缺一不可。

---

## 一、模式一（文生视频 T2V）选型评估

### 1.1 候选模型对比矩阵

| 评估维度 | AnimateDiff | CogVideoX | HunyuanVideo | **Wan2.1（推荐）** | LTX-Video |
|---------|-------------|-----------|--------------|------------------|-----------|
| **3秒视频生成质量** | ★★★☆ 中等偏上，SD1.5底座限制上限 | ★★★★ 良好，5B版质量扎实 | ★★★★★ 顶级，电影级运动 | ★★★★★ 顶级，VBench 86.22%超越Sora | ★★★☆ 中等，速度换质量 |
| **6GB显存** | ✅ 可跑(512²,16帧) | ⚠️ 仅2B+GGUF勉强 | ❌ 不可行 | ✅ 14B GGUF Q3可跑 | ✅ 蒸馏版可跑 |
| **8GB显存** | ✅ 流畅 | ✅ 2B fp16 / 5B FP8 | ⚠️ GGUF勉强,质量降 | ✅ 14B GGUF Q4/Q5流畅 | ✅ 流畅 |
| **12GB显存** | ✅ 富余 | ✅ 5B bf16 | ⚠️ GGUF Q6勉强 | ✅ 14B GGUF Q6/Q8高质量 | ✅ 富余 |
| **24GB显存** | ✅ 过剩 | ✅ 5B满血 | ✅ 原生bf16满血 | ✅ 14B原声bf16满血 | ✅ 过剩 |
| **推理速度(3s视频)** | 2-4min | 3-6min(5B) | 8-12min | 3-5min(GGUF) / 6min(满血) | **0.5-1min**最快 |
| **ComfyUI兼容性** | ★★★★★ 极成熟 | ★★★★ Kijai封装完善 | ★★★★ 官方原生支持 | ★★★★★ 官方原生+Kijai封装 | ★★★★ 官方支持 |
| **安装包体积影响** | 小(SD1.5+motion≈3.5GB) | 大(5B≈10GB) | 极大(13B+编码器≈25GB) | 中(1.3B≈2.6GB / 14B GGUF≈8GB) | 中(2B≈4GB) |
| **MVP推荐度** | ★★★ 保底方案 | ★★★ 备选 | ★★ 太重,留云端 | ★★★★★ **首选** | ★★★★ 速览/预览用 |

### 1.2 推荐最优模型 + 理由

**首选：Wan2.1 1.3B fp16 + Wan2.1 14B GGUF（双档配置）**

- **为什么选 Wan2.1**：
  - 质量天花板：VBench 86.22%，是当前开源 T2V 最高分，画质和运动连贯性直接对标商业级。
  - 显存友好：1.3B 版本仅需 8.19GB 显存即可 480p 出片，14B 经 GGUF Q4 量化后 8GB 显存可跑 720p——完美命中 NexusVideo 的 6-8GB 目标用户群。
  - 许可干净：Apache 2.0，商用无障碍。
  - 生态完备：ComfyUI 官方原生支持 + Kijai 的 WanVideoWrapper，SageAttention 提速 50%、TeaCache 提速 20% 均已就绪。
  - 中文友好：首个支持视频内中英文字生成的模型，对国内小白用户场景天然加分。
- **为什么保留 AnimateDiff 作保底**：6GB 显存的 GTX1060 等老卡，Wan2.1 即使 Q3 量化也偏吃力，AnimateDiff 基于 SD1.5 极轻量，是这类设备的"能跑就行"兜底。
- **为什么不选 HunyuanVideo 做 T2V 主力**：13B 参数原生需 20-26GB，即便 GGUF 压到 8GB 质量损耗明显且速度慢（8-12 分钟/3 秒视频），小白用户等不及。HunyuanVideo 更适合作为**云端 GPU 的高端选项**（交由 devops 评估）。

### 1.3 默认参数推荐（T2V）

#### 模型参数推荐表

| 参数 | 推荐区间 | 默认值 | 影响说明 |
|------|---------|--------|---------|
| steps（采样步数） | 20-30 | **25** | Wan2.1 收敛快，25步质量/速度平衡点；>30 收益递减；低端机可降到20 |
| cfg（CFG Scale） | 3.0-7.5 | **5.0**（Wan）/ 7.0（AnimateDiff） | Wan2.1 对 CFG 敏感，>7 易过饱和闪烁；AnimateDiff 用 6-8 |
| sampler（采样器） | Euler / DPM++ 2M | **Euler** | Wan 原生工作流用 Euler 简单稳定；CogVideoX 用 CogVideoXDDIM |
| scheduler | normal / karras | **normal** | 视频模型对噪声调度敏感，保持默认 normal 最稳 |
| 分辨率 | 480p / 720p | **832×480(480p)** | 6-8GB 用 480p；12GB+ 升 720p(1280×720) |
| 帧数 | 25 / 49 / 81 | **25帧** | 3秒×8fps≈25帧；帧数须满足 4n+1 或 8n+1 格式 |
| 帧率(fps) | 8 / 16 | **8fps** | 25帧÷8fps≈3秒；后期可用 RIFE 插帧补到 24fps 提升流畅感 |
| 精度 | fp16 / bf16 / GGUF | **fp16**(1.3B) / **GGUF Q4**(14B) | 1.3B 用 fp16 最快；14B 低端用 GGUF Q4，高端用 bf16 |
| 负向提示词 | — | `模糊,低质量,变形,水印,文字,静态画面,闪烁` | 抑制常见瑕疵 |

### 1.4 3秒视频帧数与分辨率平衡方案

| 目标机型 | 帧数 | fps | 实际时长 | 分辨率 | 说明 |
|---------|------|-----|---------|--------|------|
| 6GB | 17帧 | 8fps | ~2秒 | 512×512 | 极限压缩，AnimateDiff 保底 |
| 8GB | 25帧 | 8fps | ~3秒 | 832×480 | **MVP 默认档**，Wan2.1 14B GGUF Q4 |
| 12GB | 25帧 | 8fps | ~3秒 | 1280×720 | 高清档，Wan2.1 14B GGUF Q6 |
| 24GB | 49帧 | 16fps | ~3秒 | 1280×720 | 旗舰档，满血流畅，可后续插帧到 24fps |

> **关键建议**：MVP 全局默认输出"8fps × 25帧 ≈ 3秒 480p"，生成后用 RIFE 帧插值补到 24fps 再交付用户。这样底层只生成 25 帧（省显存省时间），但用户看到的成品是流畅的 24fps——**用插帧换显存，是小白用户无感知的质量/成本最优解**。

---

## 二、模式二（图生视频 I2V）选型评估

### 2.1 组合方案验证

#### 方案 A：AnimateDiff + ControlNet (Tile) ✅ 验证通过

- **原理**：Tile ControlNet 将输入图分块提取结构信息，约束生成过程保持原图结构，同时 AnimateDiff 的 motion module 注入时序运动。
- **可行性**：★★★★★ 高。社区成熟，Kosinkadink 的 ComfyUI-AnimateDiff-Evolved 原生支持。6GB 可跑 512×512 / 16帧。
- **适用**：保持原图构图，仅添加局部动态（风吹、水流、眨眼）。
- **风险**：Tile 强度过高会导致运动僵硬；过低则结构漂移。建议 Tile weight 0.5-0.7。

#### 方案 B：AnimateDiff + IP-Adapter ✅ 验证通过

- **原理**：IP-Adapter 将参考图的风格/语义特征作为图像提示注入，AnimateDiff 负责运动生成。比 Tile 更自由，能做"参考这张图的风格，但运动方式自由发挥"。
- **可行性**：★★★★★ 高。ComfyUI_IPAdapter_plus 完善，ip_strength 0.5-1.0 可调。
- **适用**：风格迁移式 I2V，或"一张照片→生成同风格动态视频"。
- **风险**：ip_strength >1.2 易压制文本提示导致内容失控；保持 0.6-0.8 最稳。

#### 方案 C（推荐升级路径）：Wan2.1-I2V GGUF ✅ 验证通过

- **原理**：Wan2.1 原生 I2V 模型，首帧锚定 + 时序扩散，质量显著高于 AnimateDiff 方案。
- **可行性**：★★★★ 高。`wan2.1-i2v-14b-480p-Q6_K.gguf` 约 6-8GB 显存可跑。
- **适用**：8GB+ 设备的 I2V 质量升级档。
- **建议**：MVP 阶段 6GB 用方案 A/B，8GB+ 自动切方案 C。

### 2.2 "运动强度"滑块（1~10）→ Denoising Strength 映射

> **工程翻译**：Denoising Strength（降噪强度）控制生成时对原图/首帧的"改写程度"。值越低越接近原图（微动），值越高运动越自由但越容易脱离原图。注意：I2V 中 denoise=1.0 意味着完全重新生成（忽略首帧），所以上限不必到 1.0。

| 运动强度档位 | Denoising Strength | 效果描述 | 适用场景 |
|------------|-------------------|---------|---------|
| **1-3（微动）** | **0.35 - 0.50** | 原图几乎不变，仅局部细微动态（发丝飘动、水面涟漪、眨眼） | 静态照片"活过来"、产品展示微动 |
| **4-7（明显运动）** | **0.50 - 0.70** | 清晰可见的运动，构图仍可辨认（人物转头、镜头平移、物体飘落） | 通用 I2V 主力区间，**滑块默认值 5 → denoise 0.55** |
| **8-10（剧烈运动）** | **0.70 - 0.90** | 大幅运动，可能偏离原图（奔跑、爆炸、场景变换） | 创意表达，需接受画面与原图差异增大 |

**映射公式建议（供前端滑块实现）**：
```
denoising = 0.30 + (motion_strength / 10) × 0.60
// motion_strength=1 → 0.36；motion_strength=5 → 0.60；motion_strength=10 → 0.90
```

> **避坑提醒**：denoise >0.90 时画面闪烁和结构崩坏概率陡增，建议硬上限设 0.90。小白用户拉到 10 也只到 0.90，避免"一拉到底就糊"的差体验。

### 2.3 图生视频默认参数推荐

| 参数 | 推荐区间 | 默认值 | 影响说明 |
|------|---------|--------|---------|
| denoising_strength | 0.35-0.90 | **0.55** | 由运动强度滑块映射，见上表 |
| steps | 20-25 | **20** | I2V 比 T2V 收敛快，20步足够 |
| cfg | 6.0-8.0 | **7.0** | AnimateDiff 用 6-8；过高原地闪烁 |
| motion_module | mm_sd_v15_v2 | v2 | AnimateDiff 运动模块，v2 运动更自然 |
| ControlNet Tile weight | 0.5-0.7 | **0.6** | 结构保持力，与 denoise 反向相关 |
| IP-Adapter strength | 0.5-0.8 | **0.65** | 风格参考强度 |
| 帧数 | 16-25 | **16**(低端)/25(中端) | 6GB 用16帧，8GB+ 用25帧 |
| 分辨率 | 512² / 640×480 | **512×512** | 与输入图分辨率对齐 |
| 采样器 | Euler a / DPM++ 2M Karras | **Euler a** | AnimateDiff 标配 |

---

## 三、模式三（视频风格化 V2V）选型评估

### 3.1 Video-to-Video + ControlNet 保结构换纹理方案

**核心思路**：抽取源视频每帧 → 用 ControlNet 提取结构约束（边缘/深度/姿态）→ 用风格化 prompt 重新生成每帧 → 重组装为视频。只换"纹理/画风"，保留"结构/运动"。

### 3.2 可用 ControlNet 类型推荐

| ControlNet 类型 | 用途 | 推荐强度 | 适用场景 | MVP 是否纳入 |
|----------------|------|---------|---------|-------------|
| **Depth（深度）** | 保留前后景空间关系，防背景压缩拉伸 | 0.7-0.8 | 通用首选，几乎万金油 | ✅ MVP 必装 |
| **Canny（边缘）** | 保留物体轮廓，锁硬表面结构 | 0.4-0.6 | 建筑/机械/场景稳定 | ✅ MVP 必装 |
| OpenPose（姿态） | 锁定人物关节姿态 | 0.6-0.8 | 人物动作视频 | ⏸ V2（需人物检测） |
| Lineart（线稿） | 类 Canny 但更艺术化 | 0.5-0.7 | 动漫化风格 | ⏸ V2 |
| Tile | 高清修复保细节 | 0.5-0.6 | 风格化后画质增强 | ⏸ V2 |

**MVP 推荐 ControlNet 组合**：单 Depth（weight 0.75）即可覆盖 80% 风格化场景。多 ControlNet 堆叠（Depth+Canny+Pose）需 12-16GB+ 显存，与目标机型冲突，推迟 V2。

### 3.3 风格预设 prompt 模板设计

> **设计原则**：每个预设在后端拼装为完整的正向+负向 prompt，前端只展示"油画/3D/水墨"三个按钮。模板用 `{user_desc}` 占位用户输入。

#### 预设 1：油画风格
```
正向：{user_desc}, oil painting style, thick visible brushstrokes, rich textured canvas,
      classical fine art, baroque lighting, masterpiece, gallery quality, 8k
负向：photo, realistic, 3d render, digital art, flat colors, blurry, low quality, watermark
denoising: 0.55 | CFG: 7.0 | ControlNet: Depth@0.75
```

#### 预设 2：3D 卡通风格
```
正向：{user_desc}, 3d pixar animation style, octane render, soft studio lighting,
      vibrant saturated colors, stylized characters, smooth subsurface scattering, 8k
负向：photo, realistic, dark, gritty, 2d flat, sketch, low quality, watermark
denoising: 0.60 | CFG: 7.5 | ControlNet: Depth@0.75
```

#### 预设 3：水墨风格
```
正向：{user_desc}, traditional chinese ink wash painting, sumi-e, minimalist brush strokes,
      monochrome with subtle color accents, xuan paper texture, negative space, zen aesthetic
负向：photo, realistic, 3d, vibrant saturated colors, digital art, busy background, watermark
denoising: 0.55 | CFG: 6.5 | ControlNet: Depth@0.75 + Canny@0.4
```

### 3.4 MVP 阶段可行性判断

**结论：MVP 纳入"轻量版 V2V"，完整版推迟 V2。**

| 维度 | MVP 轻量版（建议纳入） | 完整版（推迟 V2） |
|------|---------------------|------------------|
| 输入 | 短视频（≤5秒, ≤480p） | 任意长度视频 |
| ControlNet | 单 Depth | Depth+Canny+OpenPose 堆叠 |
| 分辨率 | 512×512 | 720p+ |
| 风格预设 | 油画/3D/水墨 3个 | 8-10个含赛博/动漫/写实 |
| 显存要求 | 8GB+ | 12-16GB+ |
| 工程复杂度 | 中（单 CN，流程可控） | 高（多CN调度+帧对齐+闪烁抑制） |

**理由**：
1. **显存红线冲突**：多 ControlNet 堆叠需 12-16GB，NexusVideo 目标用户多为 6-8GB，强行上会大面积爆显存。
2. **小白体验冲突**：V2V 本质是"高级玩法"，完整版需要用户理解 ControlNet 强度调节，违背"3步出片"极简目标。轻量版（选视频→选风格→出片）刚好 3 步。
3. **工程节奏**：MVP 8 周紧，T2V+I2V 是核心卖点，V2V 轻量版足以演示能力完整性，完整版留 V2 作为升级卖点。
4. **趋势预判**：Wan2.2 Fun Control（Wan 的 ControlNet 版）2025 年下半年正在成熟，V2 阶段可直接用 Wan 原生 V2V 替代 AnimateDiff 方案，质量跃升——现在不必在 AnimateDiff V2V 上过度投入。

---

## 四、显存优化策略

### 4.1 显存分级方案

| 显存级别 | 可用模式 | 推荐分辨率 | 优化手段 | 预期生成时间(3s视频) |
|---------|---------|-----------|---------|-------------------|
| **6GB** (GTX1060等) | T2V(AnimateDiff/Wan Q3) + I2V(AnimateDiff) | 512×512, 16帧@8fps | xformers + fp16 + GGUF Q3 + Tiled VAE + CPU offload + 关闭次要CN | T2V 3-6min / I2V 4-8min |
| **8GB** (RTX3060/4060等) | T2V(Wan 14B GGUF Q4) + I2V(AnimateDiff+CN / Wan I2V Q4) + V2V轻量 | 832×480, 25帧@8fps | fp16/fp8 + GGUF Q4/Q5 + Tiled VAE + TeaCache | T2V 2-4min / I2V 3-5min / V2V 5-8min |
| **12GB** (RTX3060 12G/4070等) | 全模式 + Wan 14B GGUF Q6/Q8 高清 | 1280×720, 25-49帧 | fp8 + TeaCache + SageAttention | T2V 1.5-3min / I2V 2-4min / V2V 4-6min |
| **24GB** (RTX3090/4090等) | 全模式满血 + HunyuanVideo(云端同款) | 1280×720-1080p, 49帧+ | bf16 满血 + SageAttention + fullgraph编译 | T2V 1-2min / I2V 1.5-3min / V2V 3-5min |

### 4.2 优化手段详解（可操作参数）

#### ① xformers / SDPA 注意力优化
- **作用**：降低注意力计算的显存峰值约 30-40%，几乎无质量损失。
- **启用方式**：ComfyUI 启动参数加 `--use-pytorch-cross-attention`（默认 SDPA 已含），或安装 xformers 后自动启用。
- **适用**：所有显存级别，6GB 必开。

#### ② fp16 / bf16 半精度
- **作用**：模型权重显存减半，速度提升约 30-40%。
- **启用方式**：模型加载节点选 fp16/bf16 版本；1.3B 用 fp16，14B/5B 用 bf16（bf16 需 compute capability 7.0+ 即 Volta 架构以上）。
- **注意**：CogVideoX 1.5 版不支持 fp16，必须 bf16。

#### ③ GGUF 量化（低显存核心手段）
- **作用**：将 14B 模型压缩到 4-8GB 显存可运行，是 Wan2.1 在消费级显卡落地的关键。
- **量化档位选择**：
  - 4GB → Q3_K_S（质量损失明显，仅保底）
  - 6GB → Q3_K_M
  - 8GB → Q4_1 / Q5_K_M（**推荐，质量/显存最佳平衡**）
  - 12GB+ → Q6_K / Q8_0（接近无损）
- **启用方式**：安装 ComfyUI-GGUF 节点，用 UnetLoaderGGUF 替代标准 loader。

#### ④ Tiled VAE（分块 VAE 解码）
- **作用**：VAE 解码阶段分块处理，避免高分辨率解码爆显存。
- **启用方式**：用 VAEDecodeTiled 替代 VAEDecode，设 tile_size 96 / overlap 0.083。
- **适用**：所有高分辨率输出，6-8GB 必开。

#### ⑤ TeaCache / FasterCache（推理加速）
- **作用**：缓存中间注意力结果，减少冗余计算，提速 20-50%。
- **启用方式**：CogVideoX 在 sampler 节点开启 TeaCache；Wan2.1 通过 WanVideoWrapper 启用。
- **适用**：8GB+ 推荐，6GB 收益有限（本身计算量已小）。

#### ⑥ CPU Offload（显存不够用内存顶）
- **作用**：将不活跃的模型权重卸载到 CPU 内存，牺牲速度换显存。
- **启用方式**：ComfyUI 启动参数 `--lowvram`（激进卸载）或 `--medvram`（适度）。
- **代价**：生成速度慢 40-60%，仅 6GB 极端场景兜底。

### 4.3 低显存降级方案（自动降级链）

当生成时检测到显存不足（OOM），按以下顺序自动降级：

```
OOM 触发 → 降分辨率(720p→480p) → 降帧数(49→25→16) → 降步数(25→20→15)
         → 切换量化档(Q6→Q4→Q3) → 开启CPU offload → 仍失败则提示"建议使用云端"
```

> **给 python-backend-core**：建议在 FastAPI 中转层实现显存探测（`torch.cuda.mem_get_info()`），首次启动时探测显存并自动选择对应档位的默认参数，小白用户无需手动调参。

---

## 五、自定义节点清单

### 5.1 必装节点（9个）

| 序号 | 节点名称 | GitHub 仓库 | 用途 | 版本兼容性 |
|------|---------|------------|------|-----------|
| 1 | **ComfyUI-Manager** | ltdrdata/ComfyUI-Manager | 节点管理器，安装/更新/缺失检测，ComfyUI 生态入口 | 全版本，必装首位 |
| 2 | **ComfyUI-AnimateDiff-Evolved** | Kosinkadink/ComfyUI-AnimateDiff-Evolved | AnimateDiff 核心，T2V/I2V/V2V 运动生成，I2V保底引擎 | 成熟稳定，SD1.5生态 |
| 3 | **ComfyUI-WanVideoWrapper** | kijai/ComfyUI-WanVideoWrapper | Wan2.1/2.2 模型封装，T2V首选引擎，支持SageAttn/TeaCache | 2025新，需ComfyUI≥0.3.x |
| 4 | **ComfyUI-GGUF** | city96/ComfyUI-GGUF | GGUF量化模型加载，低显存核心依赖 | 稳定，与Wan/CogVideoX配合 |
| 5 | **ComfyUI-VideoHelperSuite** | Kosinkadink/ComfyUI-VideoHelperSuite | 视频加载/分帧/合成MP4输出，视频IO水电煤 | 极成熟，必装 |
| 6 | **ComfyUI-Advanced-ControlNet** | Kosinkadink/ComfyUI-Advanced-ControlNet | ControlNet 应用与调度，I2V/V2V结构控制 | 成熟 |
| 7 | **comfyui_controlnet_aux** | Fannovel16/comfyui_controlnet_aux | ControlNet预处理器(Canny/Depth/OpenPose/Lineart提取) | 成熟 |
| 8 | **ComfyUI_IPAdapter_plus** | cubiq/ComfyUI_IPAdapter_plus | IP-Adapter图像提示，I2V风格参考 | 成熟，需配套模型 |
| 9 | **ComfyUI-Frame-Interpolation** | Fannovel16/ComfyUI-Frame-Interpolation | RIFE帧插值，8fps补到24fps提升流畅度 | 成熟 |

### 5.2 推荐节点（4个，增强体验）

| 序号 | 节点名称 | 用途 | 说明 |
|------|---------|------|------|
| 10 | ComfyUI-Impact-Pack | 图像批处理/放大/检测工具集 | V2V分帧处理辅助 |
| 11 | ComfyUI-KJNodes | Kijai综合工具节点 | 视频处理辅助，与Wan/CogVideoX配套 |
| 12 | was-node-suite-comfyui | 综合图像处理工具 | 通用辅助 |
| 13 | ComfyUI_essentials | 基础工具集 | 通用辅助 |

### 5.3 安装顺序与依赖关系

```
ComfyUI-Manager（第1步，所有后续通过它装）
   │
   ├── 核心引擎层（无依赖，可并行）
   │    ├── ComfyUI-AnimateDiff-Evolved
   │    ├── ComfyUI-WanVideoWrapper  ← 需 ComfyUI ≥ v0.3.x，先 update ComfyUI
   │    └── ComfyUI-GGUF
   │
   ├── 视频IO层（无依赖）
   │    └── ComfyUI-VideoHelperSuite
   │
   ├── 控制层（互不依赖）
   │    ├── ComfyUI-Advanced-ControlNet
   │    ├── comfyui_controlnet_aux
   │    └── ComfyUI_IPAdapter_plus  ← 需额外下载 IP-Adapter 模型到 models/ipadapter
   │
   ├── 后处理层
   │    └── ComfyUI-Frame-Interpolation
   │
   └── 辅助层（最后，可选）
        ├── ComfyUI-Impact-Pack
        ├── ComfyUI-KJNodes
        ├── was-node-suite-comfyui
        └── ComfyUI_essentials
```

**关键依赖提醒**：
- ComfyUI-WanVideoWrapper 要求 ComfyUI 主程序 ≥ v0.3.x，**封装便携版时务必先 update ComfyUI 到最新**，否则节点加载报错。
- comfyui_controlnet_aux 需额外下载 ControlNet 模型权重（Canny/Depth）到 `models/controlnet`。
- ComfyUI_IPAdapter_plus 需下载 IP-Adapter 模型到 `models/ipadapter`（含 ip-adapter_sd15 / ip-adapter_plus）。
- ComfyUI-Frame-Interpolation 需下载 RIFE 模型（如 `rife49.pth`）到 `models/frame_interpolation`。

### 5.4 版本锁定建议

> **给 devops-engineer / python-backend-core**：ComfyUI 和自定义节点迭代极快，版本漂移是白皮书列明的高风险。建议：
> - 封装便携版时记录每个 custom_node 的 git commit hash，写入 `versions.json`。
> - ComfyUI 主程序锁定到一个验证过的稳定 tag（建议 v0.3.39 或更高稳定版）。
> - 提供 `update_comfyui_and_python_dependencies.bat` 但默认不自动更新，避免小白用户一键更新后节点不兼容。

---

## 六、生成质量基线建议

### 6.1 各模式"合格线"标准

| 评估维度 | T2V 合格线 | I2V 合格线 | V2V 合格线 |
|---------|-----------|-----------|-----------|
| **画面连贯性** | 相邻帧无突变，主体形态稳定不"融化" | 首帧与后续帧主体一致，无明显身份漂移 | 全程主体身份/结构保持，无突变 |
| **运动流畅度** | 运动方向合理，无卡顿/抽帧感 | 运动幅度与运动强度档位匹配 | 运动轨迹与源视频一致，无错位 |
| **清晰度** | 480p 无明显模糊块，纹理可辨 | 同 T2V，且不劣于输入图清晰度 | 不劣于源视频清晰度 |
| **prompt 遵循度** | 主体/场景/风格 3要素至少命中2个 | 运动描述与生成动作基本一致 | 风格预设特征明显可辨 |
| **闪烁/噪点** | 无全屏闪烁，无规律性色块跳变 | 无首帧到第二帧的突变闪烁 | 无帧间纹理跳变 |
| **时延红线** | ≤5min(8GB) / ≤3min(12GB) | ≤5min(8GB) | ≤8min(8GB) |

### 6.2 常见生成问题与调参方向

| 问题现象 | 根因 | 调参方向 |
|---------|------|---------|
| **画面闪烁/抖动** | CFG 过高 或 步数过多 或 无时序一致性约束 | 降 CFG 到 5-6（Wan）/ 6-7（AD）；降步数到 20；开启 motion module 的 temporal consistency；后处理用 RIFE 插帧平滑 |
| **运动不自然/卡顿** | 帧率过低 或 运动模块不匹配 或 denoise 过低 | 提升 fps（插帧到 24fps）；换 motion_module 版本；I2V 适当提高 denoising |
| **主体"融化"/形变** | denoise 过高 或 ControlNet 强度不足 | I2V/V2V 降 denoising 到 0.5 以下；提高 Depth ControlNet weight 到 0.75+ |
| **色彩偏移/过饱和** | CFG 过高 | 降 CFG；Wan2.1 保持 ≤7.0 |
| **结构崩坏/肢体扭曲** | 无姿态约束 或 多帧漂移 | 加 OpenPose ControlNet（V2）；降帧数；提高 Depth 强度 |
| **生成超时/卡死** | 显存不足 OOM 或 步数过多 | 按降级链降分辨率/帧数/步数；开启 Tiled VAE 和 CPU offload |
| **文本不遵循(生成内容与prompt无关)** | prompt 过长/过短 或 CFG 过低 | Wan2.1 用结构化 prompt：[主体],[动作],[场景],[风格],[质量]；CFG 提到 5.0+ |
| **V2V 风格"贴不上"** | denoise 过低 或 ControlNet 过强压制风格 | 提 denoising 到 0.55-0.65；降 Depth weight 到 0.6-0.7 |

---

## 七、安装包体积与模型清单（给 devops / python-backend-core）

### 7.1 分层打包策略

| 层级 | 内容 | 体积估算 | 打包方式 |
|------|------|---------|---------|
| **运行时层** | PyTorch + CUDA + ComfyUI 主程序 + 9个必装节点 | ~5-6GB | 随安装包内置 |
| **基线模型层** | SD1.5 + AnimateDiff motion + 1个ControlNet(Depth) + IP-Adapter + VAE/CLIP | ~8-10GB | 随安装包内置 |
| **Wan2.1 模型层** | Wan2.1 T2V 1.3B fp16 + 14B GGUF Q4 + I2V GGUF + umt5编码器 | ~16-18GB | **首次启动可选下载** |

- 随包总体积：~15-16GB（满足白皮书 15-20GB 目标）✅
- Wan2.1 大模型走"首次启动按需下载"，避免安装包膨胀到 30GB+，降低用户下载门槛。
- 下载源建议：HuggingFace + 国内镜像（如 modelscope）双源，devops 评估 CDN。

### 7.2 模型文件清单与放置路径

| 模型文件 | 用途 | 放置路径 | 体积 |
|---------|------|---------|------|
| v1-5-pruned-emaonly.fp16.safetensors | SD1.5底座(AnimateDiff) | models/checkpoints | ~2GB |
| mm_sd_v15_v2.ckpt | AnimateDiff运动模块 | models/animatediff_models | ~1.5GB |
| control_depth_fp16.safetensors | Depth ControlNet | models/controlnet | ~1.4GB |
| ip-adapter_sd15_plus.safetensors | IP-Adapter | models/ipadapter | ~0.5GB |
| wan2.1_t2v_1.3B_fp16.safetensors | Wan T2V 轻量 | models/diffusion_models | ~2.6GB |
| wan2.1-t2v-14b-Q4_K_M.gguf | Wan T2V 量化(8GB用) | models/diffusion_models | ~8GB |
| wan2.1-i2v-14b-480p-Q5_K_M.gguf | Wan I2V 量化 | models/diffusion_models | ~8GB |
| umt5_xxl_fp8_e4m3fn_scaled.safetensors | Wan文本编码器 | models/text_encoders | ~5GB |
| wan_2.1_vae.safetensors | Wan VAE | models/vae | ~0.5GB |
| rife49.pth | RIFE插帧模型 | models/frame_interpolation | ~0.1GB |

---

## 八、技术趋势研判与行动建议（3-6个月预判）

| 时间 | 趋势预判 | 现在该做什么 | 3个月后该盯什么 |
|------|---------|------------|---------------|
| 当前(2026 Q3) | Wan2.1 已是开源T2V标杆，GGUF生态成熟 | T2V主推Wan2.1，I2V保底AnimateDiff | Wan2.2 Fun Control成熟度 |
| +3个月 | Wan2.2 / 原生1080p / 更长时长成标配 | 架构预留模型热切换能力(不写死模型路径) | 评估Wan2.2替换2.1的迁移成本 |
| +6个月 | 实时生成(LTX级)可能普及，云端HunyuanVideo v2 | 云端路由预留高端模型档位 | LTX蒸馏版能否做"实时预览+正式生成"双档 |

**风险提示**：
- 视频模型迭代极快，**切勿在代码层写死模型文件名和参数**，全部走配置文件（`models_config.json`），便于热替换。
- GGUF 量化版本依赖社区贡献者（city96），存在断更风险，建议 devops 自建量化能力或保留多个量化源。
- ComfyUI 节点 API 可能在版本更新时变动，FastAPI 中转层应对节点参数做版本兼容适配。

---

## 九、与工程团队的落地对齐事项

1. **python-backend-core（程流深）**：本报告的参数推荐表和节点清单是 Task #7 工作流 JSON 模板的直接输入。建议工作流模板中将"运动强度滑块"暴露为 `denoising_strength` 参数，由前端传入；分辨率/帧数/步数按显存档位预设 3 套模板（6GB/8GB/12GB+）。
2. **devops-engineer（唐磐石）**：云端 GPU 建议预留 HunyuanVideo 满血档（24GB+ 显存实例）作为高端云端选项；本地与云端模型清单需对齐，云端可跑完整多ControlNet V2V。
3. **client-tauri-dev（封易安）**：无需感知模型细节，但需在设置页暴露"显存档位"选择（自动探测+手动覆盖）和"Wan2.1模型下载管理"入口。

---

*报告结束。如有参数争议或需要针对特定硬件做 benchmark 验证，随时找我。*
