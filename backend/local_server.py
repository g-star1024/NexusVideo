"""
NexusVideo Backend - FastAPI 主入口
============================================================
这是整个"调度层"的入口文件（白皮书 2.1 节三层架构中的中间层）。

启动方式：
    uvicorn local_server:app --host 127.0.0.1 --port 9881

或直接运行：
    python local_server.py

启动流程：
    1. 创建 FastAPI 应用实例
    2. 注册异常处理器（统一错误响应）
    3. 注册路由（/generate, /task, /health, /comfyui/*）
    4. lifespan 事件：启动时自动拉起 ComfyUI，停止时优雅关闭
    5. 启动 uvicorn

白皮书 4.2 节核心架构：
    前端(Tauri) → FastAPI(翻译官) → ComfyUI(推理引擎)
"""

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
import uvicorn

# 确保项目根目录在 sys.path 中（支持直接运行）
sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from exceptions import NexusError, ComfyUINotRunningError

# ================================================================
# ComfyUI / 推理相关模块：可选加载（防御性，绝不阻塞注册/登录）
# ----------------------------------------------------------------
# P0：打包版后端（requirements-pack.txt 不含 torch / comfyui 运行时）下，
# 这些模块仅依赖 httpx / websockets / psutil 等轻量依赖，可正常 import；
# 但仍用 try/except 兜底——一旦未来引入重型依赖（如 torch）缺失，
# 认证链路也绝不崩溃，而是降级为"纯认证模式"。
#
# 同时读取 NEXUS_MANAGE_COMFYUI：为 false 时（Tauri/Rust 侧已接管 ComfyUI
# 生命周期，见 client/src-tauri/src/process_manager.rs），启动阶段不再尝试
# 拉起 ComfyUI 子进程，避免无效进程与日志噪声。
# ================================================================
import os

MANAGE_COMFYUI = os.getenv("NEXUS_MANAGE_COMFYUI", "true").lower() not in (
    "false", "0", "no", "off"
)

comfyui_modules_available = False
try:
    from core.process_manager import process_manager
    from core.comfyui_client import comfyui_client
    from core.comfyui_ws import ws_listener
    from core.task_manager import task_manager
    from core.inference_router import inference_router
    from core.skill_registry import skill_registry

    # 推理 / 生成相关路由（依赖上述 comfyui core 模块）
    from routers import (  # noqa: F401
        cloud_forward,
        generate,
        progress,
        skills,
        system,
        task,
        upload,
    )
    comfyui_modules_available = True
except Exception as e:  # pragma: no cover - 防御性兜底
    logger.warning(
        "ComfyUI/推理模块加载失败，将以纯认证模式运行"
        "（注册/登录不受影响）：%s", e
    )

# 认证路由：始终加载（不依赖 ComfyUI / torch）
from routers import auth  # noqa: F401

# 设置中心路由：始终加载（仅依赖轻量依赖 httpx/psutil/subprocess，
# 不依赖 ComfyUI / torch，设置页面在 ComfyUI 不可用时尤其需要）
#
# ⚠️ 注意命名冲突：`routers.settings` 模块名与上方 `config.settings` 全局变量
#    同名。Python 中 `from routers import settings` 会覆盖同名绑定。
#    解决：导入后立即从 config 恢复 settings 变量的正确引用。
from routers import settings as settings_router  # noqa: F401
from config import settings as _config_settings  # 恢复 config.settings 的引用
settings = _config_settings  # 确保下方日志配置等处使用的 settings 仍是 config 的实例


# ================================================================
# 日志配置
# ================================================================
logger.remove()
logger.add(
    sys.stderr,
    level=settings.log_level,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
           "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
)
logger.add(
    "logs/nexus_{time:YYYY-MM-DD}.log",
    level="DEBUG",
    rotation="10 MB",
    retention="7 days",
)


# ================================================================
# 应用生命周期管理
# ================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 生命周期事件。

    启动时：
        1. 自动拉起 ComfyUI 便携版子进程
        2. 等待 ComfyUI 就绪
        3. 初始化推理路由器

    停止时：
        1. 优雅停止 ComfyUI
        2. 关闭 HTTP 连接池
    """
    logger.info("=" * 60)
    logger.info("NexusVideo FastAPI 中转服务启动中...")
    logger.info(f"  FastAPI 端口：{settings.port}")
    logger.info(f"  ComfyUI 地址：{settings.comfyui_base_url}")
    logger.info(f"  推理模式：{settings.inference_mode}")
    logger.info("=" * 60)

    # --- 用户数据库初始化（P2 阶段） ---
    try:
        from core.user_db import ensure_db_ready
        ensure_db_ready()
        logger.info("用户数据库初始化完成")
    except Exception as e:
        logger.warning(f"用户数据库初始化失败（将使用无鉴权模式）：{e}")

    # --- 技能注册表加载（不依赖 ComfyUI 进程，模块可用即可） ---
    if comfyui_modules_available:
        try:
            n = skill_registry.load_all()
            logger.info(f"Skill Registry 已加载 {n} 个内置技能")
        except Exception as e:
            logger.warning(f"Skill Registry 加载失败（不影响其它功能）：{e}")

    # --- 启动阶段（仅当 ComfyUI 模块可用时） ---
    if comfyui_modules_available and MANAGE_COMFYUI:
        try:
            # 自动启动 ComfyUI（白皮书 4.1：让 ComfyUI 成为"沉默的仆人"）
            logger.info("正在启动 ComfyUI 推理引擎...")
            port = await process_manager.start()
            logger.info(f"ComfyUI 已就绪，运行在端口 {port}")
        except Exception as e:
            logger.error(f"ComfyUI 自动启动失败：{e}")
            logger.warning(
                "FastAPI 将以降级模式运行，请手动启动 ComfyUI 或检查配置。"
                "可通过 POST /comfyui/start 手动重试。"
            )

        logger.info("NexusVideo FastAPI 服务就绪，等待前端请求...")
        # 启动后台协程持续监听 ComfyUI 的实时进度事件
        # 当 WebSocket 连接失败时自动重连，不阻塞主服务
        try:
            logger.info("启动 ComfyUI WebSocket 进度监听器...")
            ws_task = asyncio.create_task(ws_listener.connect())
            app.state.ws_task = ws_task
            logger.info("ComfyUI WebSocket 进度监听器已启动")
        except Exception as e:
            logger.error(f"WebSocket 监听器启动失败：{e}")
            logger.warning("进度文案化推送将降级为 HTTP 轮询（/progress/status/{task_id}）")
    else:
        if comfyui_modules_available and not MANAGE_COMFYUI:
            logger.info(
                "NEXUS_MANAGE_COMFYUI=false：跳过 ComfyUI 拉起"
                "（由 Tauri/Rust 侧接管 ComfyUI 生命周期），技能中心仍可列出/生成。"
            )
        else:
            logger.info("ComfyUI 模块不可用：仅以认证模式运行，跳过推理引擎启动。")

    yield  # === 应用运行期 ===

    # --- 停止阶段 ---
    logger.info("NexusVideo FastAPI 正在关闭...")
    if comfyui_modules_available:
        try:
            # 优雅关闭 WebSocket 进度监听器
            await ws_listener.stop()
            # 取消后台协程
            if hasattr(app.state, "ws_task"):
                app.state.ws_task.cancel()
                try:
                    await app.state.ws_task
                except asyncio.CancelledError:
                    pass
            await process_manager.stop()
            await comfyui_client.close()
        except Exception as e:
            logger.error(f"关闭过程出错：{e}")
    logger.info("NexusVideo FastAPI 已停止")


# ================================================================
# 创建 FastAPI 应用
# ================================================================
app = FastAPI(
    title="NexusVideo 中转服务",
    description=(
        "NexusVideo 本地调度层 —— 将前端简化参数翻译为 ComfyUI 工作流，"
        "管理推理引擎生命周期，实现本地/云端无感切换。\n\n"
        "白皮书 4.2 节'翻译官'服务实现。"
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# ================================================================
# CORS 中间件（允许 Tauri 前端跨域访问）
# ================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Tauri 使用 tauri://localhost，需放开
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================================================================
# 异常处理器（统一错误响应格式）
# ================================================================
# ================================================================
# ComfyUI 未运行 — 用户友好错误（P2 阶段：设置中心联动）
# ================================================================
# 当用户在视频生成时遇到 ComfyUI 未运行，不再返回模糊的 500，
# 而是返回明确的业务错误码 14002 + 引导跳转设置中心的建议操作。
#
# 前端收到 error_code=14002 时：
#   1. 不再展示通用 500 错误弹窗
#   2. 根据 detail.suggested_action="settings" 跳转到设置中心
#   3. 展示 detail.hint 的友好提示文案
# ================================================================
@app.exception_handler(ComfyUINotRunningError)
async def comfyui_not_running_handler(
    request: Request, exc: ComfyUINotRunningError
) -> JSONResponse:
    """
    ComfyUI 未运行时的专门异常处理器。

    返回 503 Service Unavailable（语义比 500 更准确——服务暂不可用，
    而非内部崩溃）。

    前端可依赖 error_code=14002 做差异化 UI 处理：
      - 跳转设置中心
      - 展示一键启动 ComfyUI 的引导
    """
    logger.warning(
        f"ComfyUI 推理引擎未就绪：url={settings.comfyui_base_url} "
        f"path={request.url.path}"
    )
    return JSONResponse(
        status_code=503,  # Service Unavailable — 语义更准确
        content={
            "success": False,
            "error_code": "14002",  # 业务错误码：推理引擎不可用
            "message": "推理引擎未就绪，请前往设置中心检查组件状态并启动 ComfyUI",
            "detail": {
                "comfyui_url": settings.comfyui_base_url,
                "hint": "本地 ComfyUI 服务未启动",
                "suggested_action": "settings",  # 前端据此跳转设置中心
            },
        },
    )


@app.exception_handler(NexusError)
async def nexus_error_handler(request: Request, exc: NexusError) -> JSONResponse:
    """
    统一业务异常处理。

    所有 NexusError 子类异常都转换为标准 ErrorResponse 格式：
        {
            "success": false,
            "error_code": "11004",
            "message": "显存不足...",
            "detail": {...}
        }
    """
    logger.error(f"业务异常：code={exc.error_code}, msg={exc.message}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error_code": exc.error_code,
            "message": exc.message,
            "detail": exc.detail,
        },
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底异常处理：未预期的异常返回 500。"""
    logger.exception(f"未预期异常：{exc}")
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error_code": "10001",
            "message": f"内部错误：{str(exc)}",
            "detail": {},
        },
    )


# ================================================================
# 注册路由
# ================================================================
# --- P2 阶段新增路由：认证（始终注册，不依赖 ComfyUI） ---
app.include_router(auth.router)              # /api/v1/auth/*
app.include_router(settings_router.router)   # /api/v1/settings/*  设置中心

# --- 推理 / 生成相关路由：仅在 ComfyUI 模块可用时注册 ---
if comfyui_modules_available:
    app.include_router(generate.router)      # /generate
    app.include_router(task.router)          # /task/{task_id}
    app.include_router(system.router)        # /health, /comfyui/*, /inference/*
    app.include_router(progress.router)      # /progress/ws (WebSocket)
    app.include_router(upload.router)        # /upload/image, /upload/video
    app.include_router(skills.router)        # /skills, /skills/{id}/generate
    app.include_router(cloud_forward.router) # /api/v1/cloud/*
else:
    logger.warning(
        "ComfyUI 模块不可用，已跳过推理/生成路由注册（仅保留认证能力）。"
    )


# ================================================================
# 静态文件服务 — 上传文件目录
# ================================================================
# 将 ./uploads/ 目录挂载到 /static/uploads/ 路径。
# 前端可以通过 http://127.0.0.1:9881/static/uploads/{task_id}/{filename}
# 直接访问上传的文件（包括生成的视频预览）。
#
# 目录结构：
#   ./uploads/{task_id}/{filename}
#
# 云端模式（P2）扩展：
#   当 settings.inference_mode = "cloud" 时，此挂载可替换为
#   CDN 或对象存储代理，此处保留为本地模式的兜底。
_uploads_path = Path("./uploads")
_uploads_path.mkdir(parents=True, exist_ok=True)
app.mount(
    "/static/uploads",
    StaticFiles(directory=str(_uploads_path)),
    name="uploads",
)


# ================================================================
# 根路由
# ================================================================
@app.get("/", tags=["根"])
async def root() -> dict:
    """服务信息。"""
    return {
        "name": "NexusVideo 中转服务",
        "version": "0.2.0",
        "docs": "/docs",
        "endpoints": {
            # 核心生成接口
            "generate": "POST /generate",
            "task_status": "GET /task/{task_id}",
            "health": "GET /health",
            "comfyui_status": "GET /comfyui/status",
            "progress_ws": "WS /progress/ws?task_id={task_id}",
            "progress_http": "GET /progress/status/{task_id}",
            "upload_image": "POST /upload/image",
            "upload_video": "POST /upload/video",
            "static_uploads": "/static/uploads/{task_id}/{filename}",
            # P2 阶段 — 用户认证
            "auth_register": "POST /api/v1/auth/register",
            "auth_login": "POST /api/v1/auth/login",
            "auth_refresh": "POST /api/v1/auth/refresh",
            "auth_me": "GET /api/v1/auth/me",
            "auth_quota": "GET /api/v1/auth/quota",
            # P2 阶段 — 云端 API 转发
            "cloud_generate": "POST /api/v1/cloud/generate",
            "cloud_task_status": "GET /api/v1/cloud/task/{task_id}",
            "cloud_progress_ws": "WS /api/v1/cloud/progress/ws?task_id={task_id}",
            "cloud_health": "GET /api/v1/cloud/health",
            # V2 阶段 — 技能中心（Skill Registry）
            "skills_list": "GET /skills",
            "skill_detail": "GET /skills/{skill_id}",
            "skill_readiness": "GET /skills/{skill_id}/readiness",
            "skill_generate": "POST /skills/{skill_id}/generate",
            # 设置中心（P2 阶段）
            "settings_components": "GET /api/v1/settings/components",
            "settings_component_action": "POST /api/v1/settings/components/{id}/action",
            "settings_system": "GET /api/v1/settings/system",
            "settings_logs": "GET /api/v1/settings/logs",
        },
    }


# ================================================================
# 直接运行入口
# ================================================================
if __name__ == "__main__":
    uvicorn.run(
        "local_server:app",
        host=settings.host,
        port=settings.port,
        reload=False,          # 生产环境关闭热重载
        log_level=settings.log_level.lower(),
    )
