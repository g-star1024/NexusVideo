"""
NexusVideo Backend - Pydantic 数据模型
============================================================
定义所有 API 接口的请求体和响应体。
前端（Tauri/Vue3）与后端（FastAPI）通过这些模型约定数据格式。

在整体架构中的位置：被 routers 模块引用，用于请求校验和响应序列化。
这是前后端交互的"契约"。
"""

from enum import Enum
from pydantic import BaseModel, Field, field_validator


# ================================================================
# 枚举定义
# ================================================================
class GenerationMode(str, Enum):
    """生成模式（白皮书第三章三大模式）。"""
    TXT2VIDEO = "txt2video"      # 模式一：一句话出片
    IMG2VIDEO = "img2video"      # 模式二：图生视频
    VIDEO2VIDEO = "video2video"  # 模式三：视频风格化


class SkillMode(str, Enum):
    """技能生成模式标签（供前端 Gallery 展示与 TaskRecord 回填）。

    与 GenerationMode 的区别：更轻量（t2v/i2v/v2v），
    用于技能清单，不直接耦合三大模式枚举值。
    """
    T2V = "t2v"      # 文生视频
    I2V = "i2v"      # 图生视频
    V2V = "v2v"      # 视频风格化


class TaskStatus(str, Enum):
    """任务状态机。"""
    QUEUED = "queued"            # 已入队等待
    RUNNING = "running"          # 正在执行
    SUCCESS = "success"          # 成功完成
    FAILED = "failed"            # 执行失败
    TIMEOUT = "timeout"          # 超时


class InferenceMode(str, Enum):
    """推理模式（本地/云端）。"""
    LOCAL = "local"
    CLOUD = "cloud"
    AUTO = "auto"


# ================================================================
# 请求模型
# ================================================================
class GenerateRequest(BaseModel):
    """
    /generate 接口请求体。
    前端只需要传这些"人类能理解"的参数，
    后端"翻译官"会将其映射为 ComfyUI 复杂 JSON。
    """
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="用户输入的文本描述，如'赛博朋克风格的猫在雨中走'",
        examples=["赛博朋克风格的猫在雨中走，电影级光影，慢动作"],
    )
    mode: GenerationMode = Field(
        default=GenerationMode.TXT2VIDEO,
        description="生成模式",
    )
    seed: int | None = Field(
        default=None,
        ge=0,
        le=2**32 - 1,
        description="随机种子。传 None 时后端自动随机生成；"
                    "传固定值可复现同一结果",
    )
    # 以下为高级参数（前端右侧可折叠面板，小白用户可不传）
    width: int = Field(
        default=832,
        ge=256,
        le=1280,
        description="视频宽度（像素）。Wan2.1 T2V 1.3B 官方推荐 832",
    )
    height: int = Field(
        default=480,
        ge=256,
        le=1280,
        description="视频高度（像素）。Wan2.1 T2V 1.3B 官方推荐 480",
    )
    frames: int = Field(
        default=81,
        ge=9,
        le=129,
        description="视频帧数。Wan2.1 要求 4n+1，默认 81（5秒@16fps）",
    )
    steps: int = Field(
        default=30,
        ge=1,
        le=100,
        description="采样步数。Wan2.1 T2V 1.3B 推荐 30",
    )
    cfg: float = Field(
        default=6.0,
        ge=1.0,
        le=20.0,
        description="CFG Scale。Wan2.1 T2V 1.3B 推荐 6.0",
    )
    # 图生视频 / 视频风格化需要的输入（P1 阶段启用）
    input_image: str | None = Field(
        default=None,
        description="图生视频模式的输入图片路径或 base64（P1 阶段）",
    )

    # 运动强度滑块（1-10），仅 img2video / video2video 使用
    # 后端映射为 denoising_strength = 0.30 + (motion_strength/10) × 0.60
    # 硬上限 0.90，避免过度改写原图/原视频
    motion_strength: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="运动强度（1-10），仅 img2video/video2video 模式使用。"
                    "1=微动, 5=明显运动, 10=剧烈运动。后端映射为 denoising_strength。",
    )

    # 视频风格化模式（video2video）的风格预设
    # 前端只传 oil_3d_ink 中的一个，后端拼装完整的正向+负向 prompt
    style: str | None = Field(
        default=None,
        description="视频风格化模式（video2video）的风格预设：oil / 3d / ink",
    )

    # 视频风格化模式的源视频路径
    video_path: str | None = Field(
        default=None,
        description="视频风格化模式（video2video）的源视频文件路径",
    )

    # 文生视频可选模型变体：wan_fp16 / animatediff
    # 由前端根据显存档位选择，后端加载对应工作流模板
    model_variant: str = Field(
        default="wan_fp16",
        description="文生视频模型变体：wan_fp16（Wan2.1 T2V 1.3B fp16，主力）/ animatediff（AnimateDiff 保底）",
    )

    # denoising_strength 直接传入（高级用户 / API 调用方使用）
    # 与 motion_strength 二选一，denoising_strength 优先级更高
    denoising_strength: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="直接指定降噪强度（0.0-1.0）。与 motion_strength 二选一。",
    )

    # 自定义负向提示词（高级选项，不传则用默认）
    negative_prompt: str | None = Field(
        default=None,
        max_length=2000,
        description="自定义负向提示词，不传则使用模式对应的默认值",
    )

    @field_validator("prompt")
    @classmethod
    def prompt_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("提示词不能为空")
        return v.strip()


class TaskQueryRequest(BaseModel):
    """/task/{task_id} 接口可选查询参数。"""
    include_output_url: bool = Field(
        default=True,
        description="是否在响应中包含输出文件 URL",
    )


# ================================================================
# 响应模型
# ================================================================
class GenerateResponse(BaseModel):
    """/generate 接口响应体。"""
    task_id: str = Field(..., description="ComfyUI 返回的 prompt_id，用于后续状态查询")
    seed: int = Field(..., description="实际使用的随机种子（前端可显示'再来一次'用新种子）")
    mode: str = Field(..., description="生成模式")
    status: TaskStatus = Field(default=TaskStatus.QUEUED, description="初始任务状态")
    message: str = Field(default="任务已提交，正在排队", description="提示信息")


class TaskStatusResponse(BaseModel):
    """/task/{task_id} 接口响应体。"""
    task_id: str = Field(..., description="任务 ID")
    status: TaskStatus = Field(..., description="当前任务状态")
    progress: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="进度百分比（0-100）",
    )
    # 进度文案（白皮书 5.2 节：不显示"45%"，显示"正在构思画面..."）
    stage_message: str = Field(
        default="正在处理...",
        description="人类可读的进度阶段文案",
    )
    output_url: str | None = Field(
        default=None,
        description="生成完成后输出文件的访问 URL",
    )
    output_filename: str | None = Field(
        default=None,
        description="输出文件名",
    )
    error: str | None = Field(
        default=None,
        description="失败时的错误信息",
    )
    error_code: str | None = Field(
        default=None,
        description="失败时的错误码（见 ErrorCode）",
    )
    created_at: str | None = Field(default=None, description="任务创建时间")
    completed_at: str | None = Field(default=None, description="任务完成时间")
    elapsed_seconds: float | None = Field(
        default=None, description="任务耗时（秒）"
    )


class HealthResponse(BaseModel):
    """/health 接口响应体。"""
    status: str = Field(..., description="整体健康状态：healthy | degraded | unhealthy")
    comfyui: bool = Field(..., description="ComfyUI 是否在线")
    comfyui_url: str = Field(..., description="ComfyUI 地址")
    gpu_available: bool = Field(default=False, description="GPU 是否可用")
    gpu_name: str | None = Field(default=None, description="GPU 名称")
    gpu_vram_total_mb: int | None = Field(default=None, description="GPU 显存总量（MB）")
    gpu_vram_used_mb: int | None = Field(default=None, description="GPU 已用显存（MB）")
    active_tasks: int = Field(default=0, description="当前活跃任务数")
    inference_mode: str = Field(default="local", description="当前推理模式")


class ErrorResponse(BaseModel):
    """统一错误响应体。所有异常都通过此模型返回。"""
    success: bool = Field(default=False)
    error_code: str = Field(..., description="错误码")
    message: str = Field(..., description="错误描述")
    detail: dict = Field(default_factory=dict, description="附加详情")


class ComfyUIProcessStatus(BaseModel):
    """/comfyui/status 接口响应体。"""
    running: bool = Field(..., description="ComfyUI 进程是否在运行")
    pid: int | None = Field(default=None, description="进程 PID")
    port: int = Field(..., description="监听端口")
    uptime_seconds: float | None = Field(default=None, description="已运行时长（秒）")
    cpu_percent: float | None = Field(default=None, description="CPU 占用率")
    memory_mb: float | None = Field(default=None, description="内存占用（MB）")


# ================================================================
# 技能中心（Skill Registry）相关模型
# ================================================================
class SkillModelDep(BaseModel):
    """技能依赖的模型文件声明。"""
    name: str = Field(..., description="模型文件名，如 wan2.1-t2v-1.3b_fp16.safetensors")
    type: str = Field(
        default="model",
        description="模型类型：unet | clip | vae | lora | checkpoint",
    )
    min_vram_mb: int | None = Field(default=None, description="最低显存需求（MB）")
    source: str | None = Field(default=None, description="建议下载来源 URL")


class SkillMeta(BaseModel):
    """/skills 列表接口返回的技能摘要（供前端 Gallery 卡片展示）。"""
    id: str = Field(..., description="技能唯一 slug，用于 URL 与目录名")
    name: str = Field(..., description="展示名")
    category: str = Field(..., description="分类：comic | image | video | audio | utility")
    description: str = Field(..., description="一句话描述")
    mode: SkillMode = Field(..., description="生成模式标签 t2v|i2v|v2v")
    risk_tier: str = Field(default="safe", description="风险档位：safe | moderate | high")
    cloud: bool = Field(default=False, description="是否仅支持云端推理（本地无法运行）")
    thumbnail: str | None = Field(default=None, description="缩略图相对路径（走静态路由）")
    required_models: list[str] = Field(
        default_factory=list, description="依赖模型文件名列表（仅名称，供展示）"
    )
    default_params: dict = Field(
        default_factory=dict, description="默认参数（前端不传时使用）"
    )


class SkillManifest(SkillMeta):
    """技能完整 manifest（GET /skills/{id} 返回）。

    注意：重写 required_models 为 list[SkillModelDep]，以承载 manifest.json
    中的完整模型依赖声明；父类 SkillMeta 的 list[str] 由 _to_meta()
    在构造列表响应时从 dict 提取 name 后填入。
    """
    required_models: list[SkillModelDep] = Field(
        default_factory=list, description="依赖模型完整声明（name/type/min_vram_mb/source）"
    )
    version: str = Field(default="1.0.0", description="技能版本")
    entry: str = Field(default="workflow.json", description="工作流 JSON 相对路径")
    required_custom_nodes: list[str] = Field(
        default_factory=list, description="依赖的 ComfyUI 自定义节点（仓库名/标识）"
    )
    param_schema: dict = Field(
        default_factory=dict, description="可调参数 → 节点字段 映射（可选）"
    )
    output: dict = Field(
        default_factory=dict, description="输出节点/格式声明（可选）"
    )
    enabled: bool = Field(default=True, description="是否启用")


class SkillGenerateRequest(BaseModel):
    """POST /skills/{id}/generate 请求体。"""
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="用户输入的文本描述",
        examples=["赛博朋克风格的猫在雨中走，电影级光影"],
    )
    params: dict = Field(
        default_factory=dict,
        description="覆盖默认参数的键值对（如 steps/cfg/width/height/seed）",
    )
    seed: int | None = Field(
        default=None,
        ge=0,
        le=2**32 - 1,
        description="随机种子。None 时后端自动随机生成",
    )


class SkillGenerateResponse(BaseModel):
    """POST /skills/{id}/generate 响应体（202 Accepted）。"""
    task_id: str = Field(..., description="ComfyUI 返回的 prompt_id")
    seed: int = Field(..., description="实际使用的随机种子")
    status: str = Field(default="queued", description="初始状态")
    skill_id: str = Field(..., description="技能 id")


class SkillReadinessResponse(BaseModel):
    """GET /skills/{id}/readiness 响应体。"""
    ready: bool = Field(..., description="依赖是否就绪")
    missing_models: list[str] = Field(default_factory=list, description="缺失模型文件名")
    missing_nodes: list[str] = Field(default_factory=list, description="缺失自定义节点")
    note: str | None = Field(default=None, description="补充说明")
