# NexusVideo V2 — Skill Registry 设计方案

> 作者：python-backend-core (程流深)
> 范围：仅设计 + 调研，本文件不改任何代码
> 关联任务：V2「Skill 内置」专项 — 把精选 ComfyUI 技能包装为开箱即用的内置能力

---

## 0. TL;DR

- 当前工作流执行链是 **`/generate` → `task_manager.submit_and_track` → `workflow_translator.translate` → `comfyui_client.submit_prompt` → ComfyUI `/prompt`**，再由后台协程轮询 `/history`。Skill Registry 只需在 `translate` 这一层做"按 skill 选模板 + 参数映射"的替换，即可零侵入复用现有派发/进度/降级全链路。
- 建议采用 **每技能一个目录 + 顶层索引** 的存储形态：`backend/skills/<skill_id>/manifest.json` + `workflow.json`，辅以可选 `required_models.txt` / `required_custom_nodes.txt`。
- 新增 API：`GET /skills`、`GET /skills/{id}`、`POST /skills/{id}/generate`（复用现有 task 派发链路）。
- **comfyui-comics-02 可行性结论：评估中的"零改造 / 原样落入"对公开制品不成立。** 公开的 `comfyui-comics-02` 是一个 **LLM-agent 提示词技能（SKILL.md）**，不是 ComfyUI API 格式工作流 JSON，且面向 **ComfyUI Cloud**，不自带节点图。真正落地需要：(a) 拿到真实的 `comic_pro` 工作流 JSON，或 (b) 自行用 SDXL+InstantID+ControlNet+IP-Adapter 重建——后者不是零改造。risk_tier 应判为 **high**。

---

## 1. 现有工作流执行链路（含文件路径 / 行号 / 函数签名）

### 1.1 请求全链路

```
前端 → POST /generate
        routers/generate.py:46  generate(request: GenerateRequest)
      → await task_manager.submit_and_track(request)            # generate.py:116
        core/task_manager.py:109  TaskManager.submit_and_track
          → workflow, seed = translator.translate(request)        # task_manager.py:132
            core/workflow_translator.py:380  WorkflowTranslator.translate
              ├─ _load_template(mode)                            # :232  加载 {mode}.json
              ├─ 构建 params 字典 (prompt/seed/steps/cfg/...)     # :405
              ├─ _process_video2video / _process_img2video        # :466 / :524
              ├─ _replace_placeholders(...)                       # :280  正则替换 {{...}} / __INT__...
              └─ _get_field_map(mode) + WORKFLOW_FIELD_MAP 应用    # :564 / :47
          → comfyui_client.submit_prompt(workflow, client_id)    # task_manager.py:139
            core/comfyui_client.py:57  ComfyUIClient.submit_prompt → POST /prompt
          → task_id = result["prompt_id"]
          → asyncio.create_task(self._track_task(task_id, workflow, request))  # :152
        → 返回 (task_id, seed) → GenerateResponse                # generate.py:118
前端 → GET /task/{task_id} 轮询  (routers/task.py:30)
    或 WS /progress/ws?task_id=  (routers/progress.py:50)
```

### 1.2 关键函数签名

| 模块 | 函数 | 签名 | 行号 |
|---|---|---|---|
| workflow_translator | `translate` | `(request: GenerateRequest) -> tuple[dict, int]` | :380 |
| workflow_translator | `_load_template` | `(mode: GenerationMode) -> dict` | :232 |
| workflow_translator | `_replace_placeholders` | `(workflow_str: str, params: dict) -> str` | :280 |
| workflow_translator | `_get_field_map` | `(mode) -> dict` | :564 |
| workflow_translator | `apply_degradation` | `(workflow, level: int=1) -> dict` | :590 |
| task_manager | `submit_and_track` | `(request) -> tuple[str,int]` | :109 |
| task_manager | `_track_task` | `(task_id, workflow, request)` 轮询 `/history` | :177 |
| comfyui_client | `submit_prompt` | `(workflow, client_id=None) -> dict` | :57 |
| comfyui_client | `health_check` | `() -> dict` | :189 |
| inference_router | `InferenceRouter.get_backend` | `() -> InferenceBackend` | :382 |
| inference_router | `InferenceRouter.decide` | `(user_context) -> (backend, meta)` | :397 |
| inference_router | `LocalBackend.submit` | `(workflow) -> str`（内部调 `submit_prompt`） | :116 |
| validate_workflow | `validate_workflow` | `(path: Path) -> dict` | :60 |

### 1.3 现有 API 形态（backend/routers/）

| 方法 | 路径 | 文件:行 | 说明 |
|---|---|---|---|
| POST | `/generate` | generate.py:31 | 主入口，body=GenerateRequest（mode∈txt2video/img2video/video2video） |
| GET | `/task/{task_id}` | task.py:21 | 任务状态轮询 |
| POST | `/task/{task_id}/cancel` | task.py:77 | 取消 |
| GET | `/health` | system.py:31 | 综合健康 |
| GET | `/comfyui/status` | system.py:89 | ComfyUI 进程状态 |
| POST | `/comfyui/start\|stop\|restart` | system.py:100/111/121 | 进程管控 |
| GET | `/comfyui/health` | system.py:180 | （上一轮新增）轻量连通性预检 |
| GET/POST | `/inference/mode` | system.py:132/142 | 推理模式 |
| GET | `/inference/suggest-cloud` | system.py:162 | 云端建议 |
| WS | `/progress/ws` | progress.py:50 | 进度推送 |
| GET | `/progress/status/{task_id}` | progress.py:189 | HTTP 兜底 |
| POST | `/upload/image` `/upload/video` | upload.py:165/261 | 文件上传 |
| POST | `/api/v1/auth/*` `/api/v1/cloud/*` | auth.py / cloud_forward.py | 认证 / 云端转发 |

### 1.4 配置与目录（重要差异）

- `config.py:90` `workflows_dir = Path(__file__).parent.parent / "workflows"` → 解析为 **`<repo_root>/workflows/`**（已含 txt2video.json / img2video.json / video2video.json）。
- `backend/workflows/` 当前**为空**，与上述配置不一致（任务描述称 backend/workflows 为空，但实际生效的是 `<root>/workflows/`）。
- **设计建议**：Skill Registry 用单一规范目录 `backend/skills/`，避免与现有三模式模板混淆；`workflow_translator` 继续服务三模式，Skill 走独立 translator 分支。

### 1.5 现有"类 manifest"结构（可复用范式）

- `WORKFLOW_FIELD_MAP`（workflow_translator.py:47）：`{模板名: {模式: {节点ID: {inputs: {comfy字段: 参名}}}}}` —— 已是声明式映射表。
- `STYLE_PRESETS`（:124）、`DEFAULT_NEGATIVE_PROMPTS`（:103）：风格 / 负向提示词预设。
- 占位符语法：`{{prompt}}`（字符串）、`__INT__seed` / `__FLOAT__cfg` / `__BOOL__flag`（类型化）——Skill 工作流直接复用。

---

## 2. Skill Registry 抽象设计

### 2.1 Manifest Schema（草稿）

每个 Skill = 一份 manifest。建议字段：

```jsonc
{
  "id": "comfyui-comics-02",            // slug，全局唯一，用于 URL / 目录名
  "name": "Image Comics 漫画分镜",      // 展示名
  "category": "comic",                  // comic | image | video | audio | utility
  "description": "一键生成 Image Comics 风格分镜/角色图",
  "version": "1.0.0",
  "risk_tier": "high",                  // safe | moderate | high
                                       // safe=原生节点即可; moderate=需少量自定义节点;
                                       // high=需多自定义节点+大模型+高显存
  "entry": "workflow.json",             // 工作流 JSON 相对路径
  "required_models": [                  // 依赖的模型文件（含建议来源）
    { "name": "sd_xl_base_1.0.safetensors", "type": "checkpoint",
      "min_vram_mb": 6000, "source": "https://..." }
  ],
  "required_custom_nodes": [            // 依赖的 ComfyUI 自定义节点仓库
    { "repo": "https://github.com/cubiq/ComfyUI_InstantID.git",
      "purpose": "角色一致性" }
  ],
  "default_params": {                   // 前端不传时使用
    "steps": 25, "cfg": 7.5, "width": 1024, "height": 1536,
    "negative_prompt": "low quality, blurry, text, watermark"
  },
  "param_schema": {                     // 前端可调参数 → 工作流占位符/节点字段 的映射
    "prompt":    { "type": "string", "required": true,  "node": "6", "field": "text" },
    "seed":      { "type": "int",    "node": "8", "field": "seed" },
    "steps":     { "type": "int",    "node": "8", "field": "steps" },
    "cfg":       { "type": "float",  "node": "8", "field": "cfg" }
  },
  "output": { "type": "image", "node": "20", "format": "png" },  // 输出节点/格式
  "fallback_mode": "img2video",        // 失败时能否退化为已有三模式之一（可选）
  "enabled": true
}
```

字段最少集（任务要求）：`id` `name` `category` `description` `required_models` `default_params` `workflow 引用(entry)` `risk_tier` —— 均已包含。

### 2.2 存放位置：单文件 registry.json vs 每技能一个目录

**结论：采用每技能一个目录 + 可选顶层索引。**

理由：
- 一个 Skill 不止一份 workflow JSON，还有模型清单、自定义节点清单、可选提示词/资源；单文件 registry 放不下这些附属物。
- 与 ComfyUI 生态习惯一致（`custom_nodes/<node>/` 目录制）。
- 新增/删除技能 = 增删目录，无需改中央大 JSON，便于版本管理。

```
backend/skills/
  registry.json                 # 可选：显式索引（也可改为目录自动发现）
  comfyui-comics-02/
    manifest.json
    workflow.json               # ComfyUI API 格式节点图（即 "comic_pro" 工作流）
    required_models.txt        # 人工可读清单（与 manifest 冗余但便于排查）
    required_custom_nodes.txt
  ai-image-generation/
    manifest.json
    workflow.json
    ...
```

> 目录自动发现优先级高于 `registry.json`；`registry.json` 仅用于"置顶排序 / 灰度开关"等控制面需求。

### 2.3 核心模块（新增，不改现有逻辑）

**`backend/core/skill_registry.py`（新）**
- `SkillRegistry.load_all()` —— 扫描 `backend/skills/*/manifest.json`
- `list_skills() -> list[SkillMeta]` —— 供 `GET /skills`
- `get_skill(id) -> SkillManifest | None`
- `build_workflow(skill_id, params) -> (workflow_dict, seed)` —— 复用 `workflow_translator` 的占位符/字段映射机制，但参数来源改为 skill 的 `param_schema` + `default_params`
- `check_readiness(skill_id) -> {ready, missing_models, missing_nodes}` —— 校验依赖（可选，供 `/skills/{id}/health`）

**`backend/routers/skills.py`（新）**
- `GET /skills` → 列出全部 skill 摘要
- `GET /skills/{skill_id}` → 完整 manifest
- `POST /skills/{skill_id}/generate` → 复用现有派发链

### 2.4 复用的派发链（关键：不重复造轮子）

现有 `TaskManager.submit_and_track` 内部会自己调 `translator.translate()`。为让 Skill 复用进度/轮询/OOM 降级，建议给 `TaskManager` 增加一个轻量方法（不改现有方法）：

```python
# core/task_manager.py 新增
async def submit_prepared_workflow(
    self, workflow: dict, skill_id: str, params: dict
) -> tuple[str, int]:
    # 与 submit_and_track 完全相同的：队列容量检查 → submit_prompt → 建 TaskRecord
    # → asyncio.create_task(_track_task(...))，但跳过 translator.translate，
    # 因为 workflow 已由 SkillRegistry.build_workflow 产好。
    # 队列/异常/错误码路径完全复用（含上一轮修的 ComfyUINotRunningError）。
```

`routers/skills.py` 的 generate 只需：
```python
manifest = skill_registry.get_skill(skill_id) or raise SkillNotFoundError
workflow, seed = skill_registry.build_workflow(skill_id, request.params)
task_id, _ = await task_manager.submit_prepared_workflow(workflow, skill_id, request.params)
return SkillGenerateResponse(task_id=task_id, seed=seed, status="queued")
```

> 这样 Skill 生成的任务，前端仍用现有的 `GET /task/{task_id}` 与 `WS /progress/ws` 查询，无需前端改 TS（符合"不碰前端"约束）。

### 2.5 新增 API 草案

```
GET  /skills
  200 → {
    "skills": [
      {"id","name","category","description","risk_tier","required_models","default_params"}
    ]
  }

GET  /skills/{skill_id}
  200 → 完整 manifest
  404 → {error_code: "SKILL_NOT_FOUND"}

GET  /skills/{skill_id}/readiness        # 可选，生成前预检
  200 → {"ready": true, "missing_models": [], "missing_nodes": []}
  200 → {"ready": false, "missing_models":["x.safetensors"], "missing_nodes":["ComfyUI_InstantID"]}

POST /skills/{skill_id}/generate
  body: {"prompt": "...", "params": {"steps":25,"cfg":7.5,"width":1024,"height":1536}, "seed": null}
  202 → {"task_id":"...", "seed":738291, "status":"queued"}
  503 → {error_code:"SKILL_DEPENDENCY_MISSING", message:"缺少模型/自定义节点", detail:{...}}  # 依赖未就绪
  503 → {error_code:"COMFYUI_UNAVAILABLE", ...}                                            # ComfyUI 未启动（复用既有）
```

> 错误码沿用 `exceptions.py` 的 `NexusError` 体系；新增 `SkillNotFoundError` / `SkillDependencyMissingError` 两个子类（保持 11xxx 体系，建议 `11006`/`11007`）。

---

## 3. skill#1 comfyui-comics-02 可行性调研

### 3.1 公开制品实情（来源：GitHub tippyentertainment/skills、LobeHub、SkillsCat）

- `skills/comfyui-comics-02/SKILL.md` 内容仅为：
  - **Global Style Prompt Template**（Image Comics 风格提示词模板，含 `--ar 16:9 --v 4 --s 1000 --no text` 等 Midjourney 风格修饰符）；
  - **Modalities JSON 模式**（如 `{"type":"character_image","character_id":"hero","prompt":...}`）。
- 它明确声明 **"via ComfyUI Cloud"** —— 面向云端 API，不是本地 `127.0.0.1:8188`。
- **仓库内不包含任何 ComfyUI API 格式工作流 JSON**（`{"6":{"class_type":...,"inputs":...}}` 形式）。
- 评估中提到的 **"comic_pro 模式"在公开 SKILL.md 中不存在**。该词更可能指向：V2 评估内部的某个变体 / 或 ComfyUI Cloud 的 `comic_pro` 云端端点，而**未开源**。

### 3.2 真正落地漫画生成需要什么（基于 ComfyUI 漫画生态，如 comfyui-comic-workflows）

若要在本地 ComfyUI 跑漫画分镜，典型依赖：
- **基础模型**：SDXL checkpoint（如 realisticVision_v5 或漫画风 SDXL）
- **LoRA**：角色一致性 LoRA
- **自定义节点**：`ComfyUI_InstantID`、`comfyui_controlnet_aux`(Fannovel16)、`ComfyUI_IPAdapter_plus`(cubiq)、`ComfyUI_essentials`
- **视频分镜(WEBM)**：`ComfyUI-AnimateDiff-Evolved` + 视频合成节点
- **音效/配音(WAV/MP3)**：ComfyUI 原生**不支持音频**，需额外音频扩展节点 —— 超出 NexusVideo 当前本地管线范围

### 3.3 结论

| 评估项 | 结论 |
|---|---|
| 能否"原样落入 backend/workflows/" | **不能**（公开制品无工作流 JSON，只有提示词模板） |
| "零改造"是否成立 | **仅提示词层面成立**；节点图/模型/自定义节点均需自备，非零改造 |
| 工作流格式兼容性 | 若拿到真实 `comic_pro` JSON，可直接复用本设计的 `SkillRegistry.build_workflow` + 既有占位符机制 |
| risk_tier | **high**（多自定义节点 + 角色 LoRA + 高显存；且依赖音频则超出本地范围） |
| 建议 | 先向 V2 评估方确认 `comic_pro` 工作流 JSON 的真实来源；若有真实 JSON → 放入 `backend/skills/comfyui-comics-02/workflow.json` 即可低成本接入；若只有 agent 提示词 → 视为"提示词预设"，工作流图仍需自行重建 |

> 补充：现有 `workflow_translator` 的 `{{prompt}}` / `__INT__seed` 占位符机制，恰是漫画工作流所需（把 `{{prompt}}` 放进 CLIPTextEncode 节点即可）。**只要拿到工作流 JSON，接入成本确实很低**——问题出在"拿到 JSON"这一步，而非接入本身。

---

## 4. 涉及文件与拟改动点汇总

| 文件 | 改动类型 | 内容 |
|---|---|---|
| `backend/core/skill_registry.py` | **新增** | Skill 加载 / 列出 / build_workflow / readiness 校验 |
| `backend/routers/skills.py` | **新增** | `GET /skills`、`GET /skills/{id}`、`GET /skills/{id}/readiness`、`POST /skills/{id}/generate` |
| `backend/core/task_manager.py` | **小改** | 新增 `submit_prepared_workflow(workflow, skill_id, params)`（复用队列/异常/降级） |
| `backend/exceptions.py` | **小改** | 新增 `SkillNotFoundError`(11006) / `SkillDependencyMissingError`(11007) |
| `backend/local_server.py` | **小改** | `app.include_router(skills.router)` |
| `backend/skills/<id>/manifest.json` + `workflow.json` | **新增** | 各 Skill 资产（首个：comfyui-comics-02，待补 workflow.json） |
| `backend/scripts/validate_workflow.py` | **小改** | 增加 `--skill <id>` 支持，校验 skill 工作流 + 依赖声明 |
| `config.py` | **小改** | 新增 `skills_dir` 配置项（默认 `backend/skills/`） |

> 注：本设计为纯方案，未执行上述任何代码改动。
