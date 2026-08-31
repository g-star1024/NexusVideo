"""
NexusVideo Backend - ComfyUI 进程管理器
============================================================
管理 ComfyUI 便携版子进程的完整生命周期：
  启动 → 健康检测 → 异常恢复 → 优雅停止

架构位置：core/process_manager.py
被 local_server.py 在启动时调用（自动拉起 ComfyUI），
被 system router 调用（手动启停 / 状态查询）。

白皮书 4.1 节关键指令：
    python main.py --headless --port 8188 --windows-foreground
"""

import asyncio
import socket
import time
from typing import Any

import psutil
from loguru import logger

from config import settings


class ComfyUIProcessManager:
    """
    ComfyUI 子进程管理器。

    职责：
      1. 启动 ComfyUI 便携版（headless 模式，禁止弹浏览器）
      2. 等待 ComfyUI HTTP 服务就绪（健康检测轮询）
      3. 检测端口冲突并自动换端口
      4. 监控进程存活，异常退出时自动重启
      5. 优雅停止（先 SIGTERM，超时后 SIGKILL）
    """

    def __init__(self):
        self._process: asyncio.subprocess.Process | None = None
        self._start_time: float | None = None
        self._port: int = settings.comfyui_port
        self._monitor_task: asyncio.Task | None = None

    # ================================================================
    # 启动 ComfyUI
    # ================================================================
    async def start(self, auto_fallback_port: bool = True) -> int:
        """
        启动 ComfyUI 子进程。

        参数：
            auto_fallback_port: 端口被占用时是否自动尝试下一个端口

        返回：实际使用的端口号

        流程：
            1. 检查是否已在运行
            2. 检测端口是否被占用（端口冲突处理）
            3. 构建启动命令（python main.py --headless --port N）
            4. 以子进程方式启动（隐藏窗口）
            5. 轮询健康检测，等待服务就绪
        """
        # Step 1: 已在运行则直接返回
        if await self.is_running():
            logger.info("ComfyUI 已在运行，跳过启动")
            return self._port

        # Step 2: 端口冲突检测与处理
        port = settings.comfyui_port
        if auto_fallback_port and self._is_port_in_use(port):
            port = self._find_available_port(port)
            logger.warning(
                f"端口 {settings.comfyui_port} 被占用，"
                f"已自动切换到 {port}"
            )
            self._port = port

        # Step 3: 构建启动命令
        cmd = self._build_start_command(port)
        logger.info(f"启动 ComfyUI：{' '.join(cmd)}")

        # Step 4: 启动子进程
        # Windows 下使用 CREATE_NO_WINDOW 标志隐藏控制台窗口
        # 白皮书 4.1：禁止 ComfyUI 弹出浏览器，以后台进程运行
        kwargs: dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        import sys
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

        self._process = await asyncio.create_subprocess_exec(
            *cmd, **kwargs
        )
        self._start_time = time.time()

        # Step 5: 启动日志监听协程（后台读取 stdout/stderr）
        asyncio.create_task(self._log_stream(self._process.stdout, "COMFYUI-OUT"))
        asyncio.create_task(self._log_stream(self._process.stderr, "COMFYUI-ERR"))

        # Step 6: 等待服务就绪
        await self._wait_for_ready()

        # Step 7: 启动进程监控（异常自动重启）
        self._monitor_task = asyncio.create_task(self._monitor())

        logger.info(f"ComfyUI 已就绪，PID={self._process.pid}, port={port}")
        return port

    # ================================================================
    # 构建启动命令
    # ================================================================
    def _build_start_command(self, port: int) -> list[str]:
        """
        构建 ComfyUI 启动命令。

        格式：
            python main.py --headless --port 8188 --windows-foreground ...
        """
        entry_path = str(Path(settings.comfyui_path) / settings.comfyui_entry)
        cmd = [
            settings.python_executable,
            entry_path,
            "--port", str(port),
        ]
        cmd.extend(settings.comfyui_extra_args)

        # 按本机实际显存追加 --lowvram / --medvram（Task #5 自动策略）。
        # 阈值（显存总量 MB）：
        #   ≤ 8192 (8GB)         → --lowvram   （最小显存占用，速度最慢但最稳）
        #   8193 – 12288 (8–12GB) → --medvram   （显存/速度折中）
        #   > 12288 (12GB)       → 不追加        （全量加载，速度最快）
        # 注意：不写死到 config.comfyui_extra_args（会拖累 12GB+ 卡），
        #       而是运行时按检测到的实际显存拼装。无 GPU / 探测失败则不追加。
        from core.vram import _get_vram_total_mb
        _vram_mb = _get_vram_total_mb()
        if _vram_mb is not None:
            if _vram_mb <= 8192:
                cmd.append("--lowvram")
            elif _vram_mb <= 12288:
                cmd.append("--medvram")
            # > 12288 不追加，保持默认全量加载

        return cmd

    # ================================================================
    # 等待服务就绪
    # ================================================================
    async def _wait_for_ready(self) -> None:
        """
        轮询健康检测，等待 ComfyUI HTTP 服务就绪。

        ComfyUI 启动需要加载模型文件，可能需要 30-120 秒。
        超过 startup_timeout 则抛出异常。
        """
        from core.comfyui_client import comfyui_client
        import httpx

        deadline = time.time() + settings.comfyui_startup_timeout
        last_error = ""

        while time.time() < deadline:
            # 检查进程是否已退出
            if self._process and self._process.returncode is not None:
                # 进程已终止，读取 stderr 获取错误信息
                stderr_data = await self._process.stderr.read()
                error_msg = stderr_data.decode("utf-8", errors="replace")[-500:]
                raise RuntimeError(
                    f"ComfyUI 进程意外退出（exit code={self._process.returncode}），"
                    f"stderr: {error_msg}"
                )

            try:
                stats = await comfyui_client.health_check()
                logger.info(
                    f"ComfyUI 就绪！版本={stats.get('system', {}).get('comfyui_version', 'unknown')}"
                )
                return
            except (httpx.ConnectError, httpx.ConnectTimeout):
                last_error = "连接被拒绝（服务可能还在启动中）"
            except Exception as e:
                last_error = str(e)

            await asyncio.sleep(2)

        raise RuntimeError(
            f"ComfyUI 启动超时（{settings.comfyui_startup_timeout}秒），"
            f"最后错误：{last_error}"
        )

    # ================================================================
    # 进程监控（异常自动重启）
    # ================================================================
    async def _monitor(self) -> None:
        """
        后台监控 ComfyUI 进程状态。
        如果进程意外退出，尝试自动重启（最多 3 次）。
        """
        restart_count = 0
        max_restarts = 3

        while True:
            await asyncio.sleep(settings.health_check_interval)

            if self._process is None:
                continue

            if self._process.returncode is not None:
                # 进程已退出
                logger.error(
                    f"ComfyUI 进程异常退出！exit code={self._process.returncode}"
                )

                if restart_count < max_restarts:
                    restart_count += 1
                    logger.warning(
                        f"尝试自动重启 ComfyUI（第 {restart_count}/{max_restarts} 次）..."
                    )
                    try:
                        await self.start()
                        restart_count = 0  # 重启成功，重置计数
                    except Exception as e:
                        logger.error(f"自动重启失败：{e}")
                else:
                    logger.critical(
                        f"ComfyUI 重启次数已达上限（{max_restarts}），"
                        f"不再自动重试，请手动检查"
                    )
                    break

    # ================================================================
    # 日志流转发
    # ================================================================
    async def _log_stream(self, stream, prefix: str) -> None:
        """读取子进程 stdout/stderr 并转发到 loguru。"""
        while True:
            line = await stream.readline()
            if not line:
                break
            msg = line.decode("utf-8", errors="replace").rstrip()
            if msg:
                logger.bind(component=prefix).info(msg)

    # ================================================================
    # 停止 ComfyUI
    # ================================================================
    async def stop(self, timeout: float = 10.0) -> None:
        """
        优雅停止 ComfyUI 进程。

        流程：
            1. 取消监控任务
            2. 发送 SIGTERM（Windows: terminate()）
            3. 等待 timeout 秒
            4. 仍未退出则 SIGKILL（Windows: kill()）
        """
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None

        if self._process is None:
            return

        logger.info(f"正在停止 ComfyUI（PID={self._process.pid}）...")

        # Step 1: SIGTERM / terminate
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=timeout)
            logger.info("ComfyUI 已正常退出")
        except asyncio.TimeoutError:
            # Step 2: 超时则强制 kill
            logger.warning(f"ComfyUI 未在 {timeout}s 内退出，强制终止")
            self._process.kill()
            await self._process.wait()
            logger.info("ComfyUI 已强制终止")

        self._process = None
        self._start_time = None

    # ================================================================
    # 状态查询
    # ================================================================
    async def is_running(self) -> bool:
        """检查 ComfyUI 进程是否在运行（进程存活 + HTTP 可达）。"""
        if self._process is None or self._process.returncode is not None:
            return False

        # 进程存活，再验证 HTTP 是否可达
        from core.comfyui_client import comfyui_client
        import httpx
        try:
            await comfyui_client.health_check()
            return True
        except Exception:
            return False

    async def get_status(self) -> dict[str, Any]:
        """获取 ComfyUI 进程详细状态。"""
        running = await self.is_running()

        result: dict[str, Any] = {
            "running": running,
            "pid": self._process.pid if self._process else None,
            "port": self._port,
            "uptime_seconds": None,
            "cpu_percent": None,
            "memory_mb": None,
        }

        if running and self._start_time:
            result["uptime_seconds"] = round(time.time() - self._start_time, 1)

        # 获取进程资源占用（通过 psutil）
        if running and self._process and self._process.pid:
            try:
                p = psutil.Process(self._process.pid)
                result["cpu_percent"] = p.cpu_percent(interval=0.5)
                result["memory_mb"] = round(p.memory_info().rss / 1024 / 1024, 1)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        return result

    # ================================================================
    # 端口工具方法
    # ================================================================
    @staticmethod
    def _is_port_in_use(port: int) -> bool:
        """检测端口是否被占用。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", port)) == 0

    @staticmethod
    def _find_available_port(start: int, max_tries: int = 10) -> int:
        """从 start 端口开始查找可用端口。"""
        for port in range(start, start + max_tries):
            if not ComfyUIProcessManager._is_port_in_use(port):
                return port
        raise RuntimeError(f"无法找到可用端口（尝试了 {start}-{start + max_tries}）")


# 全局单例
process_manager = ComfyUIProcessManager()


# 延迟导入 Path（避免顶部循环依赖）
from pathlib import Path  # noqa: E402
