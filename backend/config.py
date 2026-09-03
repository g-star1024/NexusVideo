"""
NexusVideo Backend - 配置管理
============================================================
统一管理所有可配置参数。通过环境变量覆盖默认值，
实现开发/测试/生产环境的灵活切换。

在整体架构中的位置：被所有模块导入，是整个 FastAPI 服务的"配置中枢"。
"""

import re
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import AliasChoices, Field, field_validator


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
            # 生成产物统一写到 D: 暂存区，避免占用 C: 系统盘（NexusVideo 硬性要求：禁止往系统盘落盘）
            "--output-directory", "D:/nexusvideo_staging/output",
            # 强制关闭 pinned memory + 关闭模型缓存，避免 6GB 显存提交内存不足触发 os error 1455 OOM 崩溃
            # （本机 launch 时提交内存 ≥12GB，process_manager 自动逻辑不会加这两个 flag，故在此写死）
            "--disable-pinned-memory",
            "--cache-none",
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

    # 一键拉取默认暂存根目录（ComfyUI 源码 / 自定义节点 / 模型落盘处）
    # 硬性要求：禁止往 C: 系统盘写运行时产物，默认落在 D: 暂存区。
    staging_dir: str = Field(
        default="D:/nexusvideo_staging",
        description="一键拉取默认暂存根目录（模型/节点/产物落盘处，默认 D:）"
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

    # ================================================================
    # 下载镜像源（一键拉取 ComfyUI / 安装依赖时使用）
    # ================================================================
    # 背景（真实踩坑）：文档和用户习惯都是直接写 PIP_INDEX_URL / TORCH_INDEX_URL /
    # COMFYUI_GIT_MIRROR 这类**不带前缀**的变量，而本类 env_prefix="NEXUS_" +
    # pydantic-settings 默认 extra="forbid"，结果是"文档让用户配镜像源，用户一配
    # 后端就崩溃起不来"（extra_forbidden）。
    #
    # 处置：显式声明字段，并用 AliasChoices 同时接受带前缀与不带前缀两种写法，
    # 顺序即优先级（NEXUS_ 前缀优先）。再配合下面的 extra="ignore" 兜底，
    # 保证 .env 里出现任何未声明的变量都不会再让后端崩溃。
    pip_index_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NEXUS_PIP_INDEX_URL", "PIP_INDEX_URL"),
        description="pip 镜像源（如 https://pypi.tuna.tsinghua.edu.cn/simple）",
    )
    torch_index_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "NEXUS_TORCH_INDEX_URL", "TORCH_INDEX_URL", "TORCH_CUDA_INDEX_URL"
        ),
        description="PyTorch 轮子索引（如 https://download.pytorch.org/whl/cu124 或国内镜像）",
    )
    comfyui_git_mirror: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NEXUS_COMFYUI_GIT_MIRROR", "COMFYUI_GIT_MIRROR"),
        description="ComfyUI 仓库镜像地址（Gitee 等国内镜像）",
    )
    comfyui_tarball_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NEXUS_COMFYUI_TARBALL_URL", "COMFYUI_TARBALL_URL"),
        description="ComfyUI 源码压缩包地址（本机无 Git / git 协议被屏蔽时的回退下载源）",
    )
    comfyui_source_tag: str | None = Field(
        default="master",
        validation_alias=AliasChoices("NEXUS_COMFYUI_SOURCE_TAG", "COMFYUI_SOURCE_TAG"),
        description="ComfyUI 源码版本（tag/分支/commit；默认 master）。锁定它可保证一键拉取结果可复现",
    )
    torch_version: str | None = Field(
        default="2.7.1",
        validation_alias=AliasChoices("NEXUS_TORCH_VERSION", "TORCH_VERSION"),
        description="与 ComfyUI 源码对齐的 PyTorch 版本（cu126 轮子）；master ComfyUI 需 ≥2.7",
    )

    # ================================================================
    # D2-A 根因修复：Windows 路径归一化
    # ----------------------------------------------------------------
    # 背景：用户在 Git-Bash / MSYS / Cygwin 终端里复制的路径形如
    #   /c/Users/foo/.venv-comfyui/Scripts/python.exe   （MSYS/Git-Bash）
    #   /cygdrive/c/Users/foo/comfyui                    （Cygwin）
    # 这类"伪 Unix"路径直接传给 Windows CreateProcess 会报 WinError 2
    # （系统找不到指定的文件），导致 ComfyUI 启动 / 依赖安装失败。
    # 这里在配置加载期统一归一化为 Windows 盘符路径 C:/Users/...，
    # 一次性覆盖 process_manager._build_start_command 与
    # routers/settings.py:_resolve_launch_python 两处 create_subprocess_exec。
    # 相对路径（./comfyui）、已是正确的 Windows 路径（C:/...）保持不变。
    # ================================================================
    @field_validator("comfyui_path", "python_executable", mode="before")
    @classmethod
    def _normalize_windows_path(cls, v):
        if not isinstance(v, str):
            return v
        s = v.strip()
        # MSYS/Git-Bash: /c/Users/... -> C:/Users/...
        if re.match(r"^/[a-zA-Z]/", s):
            s = s[1].upper() + ":" + s[2:]
        # Cygwin: /cygdrive/c/Users/... -> C:/Users/...
        m = re.match(r"^/cygdrive/([a-zA-Z])/", s)
        if m:
            s = m.group(1).upper() + ":" + s[m.end() - 1:]
        return s

    class Config:
        env_prefix = "NEXUS_"          # 环境变量前缀：NEXUS_HOST, NEXUS_PORT...
        env_file = ".env"
        case_sensitive = False
        # extra="ignore"：.env / 环境变量里出现未在 Settings 声明的键时静默忽略。
        #
        # 为什么必须改（pydantic-settings 2.x 默认 extra="forbid"）：
        #   用户按文档在 .env 写 PIP_INDEX_URL，后端直接 ValidationError 起不来。
        #   对面向小白的桌面端产品，"配错也起得来"远优于"配错就白屏"。
        #
        # 已知代价（权衡后接受）：环境变量名拼错时不再报错，而是被静默忽略，
        #   排查时需注意核对拼写（镜像源四类变量已显式声明，不受影响）。
        extra = "ignore"


# 全局单例，所有模块共享同一个配置实例
settings = Settings()
