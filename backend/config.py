"""
NexusVideo Backend - 配置管理
============================================================
统一管理所有可配置参数。通过环境变量覆盖默认值，
实现开发/测试/生产环境的灵活切换。

在整体架构中的位置：被所有模块导入，是整个 FastAPI 服务的"配置中枢"。
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """全局配置项，可通过环境变量覆盖。"""

    # ================================================================
    # FastAPI 服务配置
    # ================================================================
    host: str = Field(default="127.0.0.1", description="FastAPI 监听地址")
    port: int = Field(default="9881", description="FastAPI 监听端口")
    # 注意：FastAPI 端口(9881) 与 ComfyUI 端口(8188) 必须不同，避免冲突

    # ================================================================
    # ComfyUI 进程配置
    # ================================================================
    comfyui_host: str = "127.0.0.1"
    comfyui_port: int = 8188
    comfyui_base_url: str = "http://127.0.0.1:8188"
    comfyui_ws_url: str = "ws://127.0.0.1:8188/ws"

    # ComfyUI 便携版可执行路径（Tauri 打包后为相对路径）
    # 开发环境指向本地 ComfyUI 安装目录
    comfyui_path: str = Field(
        default="./comfyui",
        description="ComfyUI 便携版根目录路径"
    )
    comfyui_entry: str = Field(
        default="main.py",
        description="ComfyUI 入口文件（相对于 comfyui_path）"
    )
    python_executable: str = Field(
        default="python",
        description="启动 ComfyUI 用的 Python 解释器路径"
    )

    # ComfyUI 启动参数（白皮书 4.1 节：--headless 后台运行）
    comfyui_extra_args: list[str] = Field(
        # 注意：ComfyUI 的 argparse 不存在 --headless / --windows-foreground，
        # 带它们会 argparse 报 unrecognized arguments 并以 exit 2 退出。
        # 禁止自动开浏览器用 --disable-auto-launch；
        # 本地单机无需 --listen（监听 0.0.0.0 会触发防火墙弹窗，且暴露端口）。
        default_factory=lambda: [
            "--disable-auto-launch",
        ],
        description="ComfyUI 启动额外参数（禁止自动开浏览器，本地单机不监听 0.0.0.0）"
    )

    # ================================================================
    # 进程健康检测
    # ================================================================
    health_check_interval: int = Field(
        default=10, description="健康检测轮询间隔（秒）"
    )
    health_check_timeout: int = Field(
        default=5, description="单次健康检测超时（秒）"
    )
    comfyui_startup_timeout: int = Field(
        default=120, description="ComfyUI 启动最长等待时间（秒）"
    )

    # ================================================================
    # 任务管理
    # ================================================================
    task_timeout: int = Field(
        default=600, description="单个任务超时时间（秒），默认 10 分钟"
    )
    task_poll_interval: float = Field(
        default=1.0, description="任务状态轮询间隔（秒）"
    )
    max_concurrent_tasks: int = Field(
        default=1, description="最大并发任务数（受显存限制，P0 阶段串行）"
    )
    max_retry: int = Field(
        default=2, description="任务失败自动重试次数"
    )

    # ================================================================
    # 路径配置
    # ================================================================
    workflows_dir: Path = Field(
        default=Path(__file__).parent.parent / "workflows",
        description="ComfyUI 工作流模板目录（项目根目录下 workflows/）"
    )
    skills_dir: Path = Field(
        default=Path(__file__).parent / "skills",
        description="内置技能目录（backend/skills/），每技能一个子目录：<id>/manifest.json + workflow.json"
    )
    output_dir: Path = Field(
        default=Path("./output"),
        description="生成视频输出目录"
    )

    # ================================================================
    # 推理路由（本地/云端切换）—— P2 阶段核心
    # ================================================================
    inference_mode: str = Field(
        default="local",
        description="推理模式：local | cloud | auto"
    )
    cloud_endpoint: str = Field(
        default="",
        description="云端推理服务地址（P2 阶段填充）"
    )
    cloud_api_key: str = Field(
        default="",
        description="云端推理服务 API Key（P2 阶段填充）"
    )
    # auto 模式触发云端切换的显存阈值（MB）
    # 默认 4096：仅 <4GB 的显卡自动建议切云端；6GB 等主流消费卡默认本地运行
    # （可追加 --lowvram 进一步降占用），避免阈值恰卡 6GB 把 6GB 卡误判为"显存不足"强制上云。
    vram_threshold_mb: int = Field(
        default=4096, description="显存低于此值（默认 4GB）时 auto 模式建议切换云端"
    )

    # ================================================================
    # 用户认证 —— P2 阶段核心
    # ================================================================
    jwt_secret: str = Field(
        default="dev-secret-change-in-production",
        description="JWT 密钥（通过 NEXUS_JWT_SECRET 环境变量覆盖）"
    )

    # ================================================================
    # 日志
    # ================================================================
    log_level: str = Field(default="INFO", description="日志级别")

    class Config:
        env_prefix = "NEXUS_"          # 环境变量前缀：NEXUS_HOST, NEXUS_PORT...
        env_file = ".env"
        case_sensitive = False


# 全局单例，所有模块共享同一个配置实例
settings = Settings()
