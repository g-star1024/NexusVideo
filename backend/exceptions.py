"""
NexusVideo Backend - 异常定义
============================================================
统一异常体系，配合异常处理器返回标准化的错误响应。

在整体架构中的位置：被 routers 和 core 模块共同使用，
所有业务异常在此集中定义，确保错误码体系一致。
"""

from fastapi import HTTPException


# ================================================================
# 错误码定义（与前端约定）
# ================================================================
class ErrorCode:
    """全局错误码枚举。前两位标识模块：10=系统 11=ComfyUI 12=任务 13=工作流"""

    # --- 系统（10xxx） ---
    INTERNAL_ERROR = "10001"
    SERVICE_UNAVAILABLE = "10002"

    # --- 推理引擎可用性（14xxx） ---
    INFERENCE_ENGINE_UNAVAILABLE = "14002"  # ComfyUI 未运行，引导跳转设置中心

    # --- ComfyUI 进程（11xxx） ---
    COMFYUI_NOT_RUNNING = "11001"
    COMFYUI_STARTUP_FAILED = "11002"
    COMFYUI_TIMEOUT = "11003"
    COMFYUI_OOM = "11004"             # 爆显存
    COMFYUI_NODE_ERROR = "11005"      # 节点连接/执行错误

    # --- 技能（11xxx，接在 ComfyUI 段之后）---
    SKILL_NOT_FOUND = "11006"              # 技能不存在
    SKILL_DEPENDENCY_MISSING = "11007"    # 技能依赖（模型/自定义节点）缺失

    # --- 任务（12xxx） ---
    TASK_NOT_FOUND = "12001"
    TASK_TIMEOUT = "12002"
    TASK_QUEUE_FULL = "12003"

    # --- 工作流（13xxx） ---
    WORKFLOW_NOT_FOUND = "13001"
    WORKFLOW_TEMPLATE_INVALID = "13002"
    INVALID_INPUT = "13003"


# ================================================================
# 基础异常类
# ================================================================
class NexusError(Exception):
    """NexusVideo 统一业务异常基类。"""

    def __init__(
        self,
        message: str,
        error_code: str = ErrorCode.INTERNAL_ERROR,
        status_code: int = 500,
        detail: dict | None = None,
    ):
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.detail = detail or {}
        super().__init__(message)


# ================================================================
# ComfyUI 相关异常
# ================================================================
class ComfyUINotRunningError(NexusError):
    """ComfyUI 进程未启动或已崩溃。

    返回 503 + error_code=14002，前端据此跳转设置中心引导用户启动 ComfyUI。
    """

    def __init__(self, detail: dict | None = None):
        super().__init__(
            message="ComfyUI 推理引擎未运行，正在尝试自动启动...",
            error_code=ErrorCode.INFERENCE_ENGINE_UNAVAILABLE,
            status_code=503,
            detail=detail,
        )


class ComfyUIStartupError(NexusError):
    """ComfyUI 启动失败（端口冲突/依赖缺失/模型加载失败等）。"""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(
            message=f"ComfyUI 启动失败：{reason}",
            error_code=ErrorCode.COMFYUI_STARTUP_FAILED,
            status_code=503,
            detail=detail,
        )


class ComfyUITimeoutError(NexusError):
    """ComfyUI 任务执行超时。"""

    def __init__(self, task_id: str, timeout: int):
        super().__init__(
            message=f"任务 {task_id} 执行超时（{timeout}秒），可能由于显存不足或工作流过于复杂",
            error_code=ErrorCode.COMFYUI_TIMEOUT,
            status_code=504,
            detail={"task_id": task_id, "timeout": timeout},
        )


class ComfyUIOOMError(NexusError):
    """显存不足（Out of Memory）。"""

    def __init__(self, detail: dict | None = None):
        super().__init__(
            message="显存不足，已触发自动降级策略。如持续失败，建议切换云端模式",
            error_code=ErrorCode.COMFYUI_OOM,
            status_code=507,
            detail=detail,
        )


class ComfyUINodeError(NexusError):
    """ComfyUI 节点连接或执行错误。"""

    def __init__(self, node_id: str, error_msg: str, detail: dict | None = None):
        super().__init__(
            message=f"工作流节点 [{node_id}] 执行失败：{error_msg}",
            error_code=ErrorCode.COMFYUI_NODE_ERROR,
            status_code=422,
            detail={"node_id": node_id, **(detail or {})},
        )


# ================================================================
# 任务相关异常
# ================================================================
class TaskNotFoundError(NexusError):
    """任务不存在。"""

    def __init__(self, task_id: str):
        super().__init__(
            message=f"任务不存在：{task_id}",
            error_code=ErrorCode.TASK_NOT_FOUND,
            status_code=404,
            detail={"task_id": task_id},
        )


class TaskTimeoutError(NexusError):
    """任务超时。"""

    def __init__(self, task_id: str, timeout: int):
        super().__init__(
            message=f"任务 {task_id} 超时",
            error_code=ErrorCode.TASK_TIMEOUT,
            status_code=504,
            detail={"task_id": task_id, "timeout": timeout},
        )


class TaskQueueFullError(NexusError):
    """任务队列已满。"""

    def __init__(self, max_concurrent: int):
        super().__init__(
            message=f"任务队列已满（最大并发 {max_concurrent}），请稍后重试",
            error_code=ErrorCode.TASK_QUEUE_FULL,
            status_code=429,
            detail={"max_concurrent": max_concurrent},
        )


# ================================================================
# 工作流相关异常
# ================================================================
class WorkflowNotFoundError(NexusError):
    """工作流模板不存在。"""

    def __init__(self, workflow_name: str):
        super().__init__(
            message=f"工作流模板不存在：{workflow_name}",
            error_code=ErrorCode.WORKFLOW_NOT_FOUND,
            status_code=404,
            detail={"workflow_name": workflow_name},
        )


class WorkflowTemplateError(NexusError):
    """工作流模板格式无效。"""

    def __init__(self, reason: str):
        super().__init__(
            message=f"工作流模板无效：{reason}",
            error_code=ErrorCode.WORKFLOW_TEMPLATE_INVALID,
            status_code=500,
        )


class InvalidInputError(NexusError):
    """用户输入无效。"""

    def __init__(self, reason: str):
        super().__init__(
            message=reason,
            error_code=ErrorCode.INVALID_INPUT,
            status_code=400,
        )


# ================================================================
# 技能相关异常（Skill Registry）
# ================================================================
class SkillNotFoundError(NexusError):
    """请求的技能不存在（id 未在 Skill Registry 注册）。"""

    def __init__(self, skill_id: str):
        super().__init__(
            message=f"技能不存在：{skill_id}",
            error_code=ErrorCode.SKILL_NOT_FOUND,
            status_code=404,
            detail={"skill_id": skill_id},
        )


class SkillDependencyMissingError(NexusError):
    """技能依赖（模型 / 自定义节点）未就绪，无法生成。"""

    def __init__(
        self,
        skill_id: str,
        missing_models: list[str] | None = None,
        missing_nodes: list[str] | None = None,
    ):
        detail = {
            "skill_id": skill_id,
            "missing_models": missing_models or [],
            "missing_nodes": missing_nodes or [],
        }
        msg = f"技能 [{skill_id}] 依赖未就绪"
        if missing_models:
            msg += f"，缺少模型：{', '.join(missing_models)}"
        if missing_nodes:
            msg += f"，缺少自定义节点：{', '.join(missing_nodes)}"
        super().__init__(
            message=msg,
            error_code=ErrorCode.SKILL_DEPENDENCY_MISSING,
            status_code=503,
            detail=detail,
        )
