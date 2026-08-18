"""
NexusVideo Backend - ComfyUI API 客户端
============================================================
封装与 ComfyUI 推理引擎的所有 HTTP 通信。
这是 FastAPI 调度层 → ComfyUI 推理层的"通信管道"。

架构位置：core/comfyui_client.py
被 workflow_translator 和 task_manager 调用。

核心接口：
  - submit_prompt()   → POST /prompt      提交工作流
  - get_history()     → GET /history/{id}  查询任务结果
  - get_queue()       → GET /queue         查询队列状态
  - interrupt()       → POST /interrupt    中断当前任务
  - upload_image()    → POST /upload/image 上传图片（图生视频）
  - health_check()    → GET /system_stats  健康检测 + GPU 状态
"""

import httpx
from loguru import logger
from typing import Any

from config import settings


class ComfyUIClient:
    """
    ComfyUI HTTP API 异步客户端。

    ComfyUI 原生 API 文档：
      POST /prompt          提交工作流，返回 prompt_id
      GET  /history/{id}    查询某个 prompt 的执行历史与输出
      GET  /queue            查询当前队列（running + pending）
      POST /interrupt        中断当前正在执行的任务
      POST /upload/image     上传图片（图生视频 / ControlNet 输入）
      GET  /system_stats     系统状态（GPU 显存、设备名等）
      GET  /object_info      所有节点信息（可用于校验工作流）
    """

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or settings.comfyui_base_url
        # 使用 httpx.AsyncClient 复用连接池，提升并发性能
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                connect=settings.health_check_timeout,
                # 提交 prompt 不应超过 30s（网络/模型加载除外）
                read=30.0,
                write=30.0,
                pool=10.0,
            ),
        )

    # ================================================================
    # 提交工作流
    # ================================================================
    async def submit_prompt(
        self,
        workflow: dict[str, Any],
        client_id: str | None = None,
    ) -> dict[str, Any]:
        """
        POST /prompt —— 提交工作流到 ComfyUI。

        请求体格式（ComfyUI 约定）：
            {
                "prompt": { "6": {...}, "8": {...}, ... },  # API 格式工作流
                "client_id": "optional-uuid"                  # WebSocket 关联用
            }

        返回：
            {
                "prompt_id": "<uuid>",       # 任务唯一标识
                "number": 1,                  # 队列编号
                "node_errors": {}             # 节点错误（如有）
            }

        异常：
            httpx.ConnectError → ComfyUI 未运行
            httpx.HTTPStatusError → ComfyUI 内部错误（如节点缺失）
        """
        payload = {"prompt": workflow}
        if client_id:
            payload["client_id"] = client_id

        logger.debug(f"提交工作流到 ComfyUI，节点数={len(workflow)}")
        resp = await self._client.post("/prompt", json=payload)
        resp.raise_for_status()
        result = resp.json()

        logger.info(f"ComfyUI 已接受任务，prompt_id={result.get('prompt_id')}")
        return result

    # ================================================================
    # 查询任务历史与输出
    # ================================================================
    async def get_history(self, prompt_id: str) -> dict[str, Any] | None:
        """
        GET /history/{prompt_id} —— 查询任务执行结果。

        ComfyUI 在任务完成后将结果写入 history。
        如果任务还在运行或不存在，返回空 dict 或不含该 prompt_id。

        返回结构（关键部分）：
            {
                "<prompt_id>": {
                    "prompt": [...],
                    "outputs": {
                        "<node_id>": {
                            "gifs": [{"filename": "xxx.gif", "subfolder": "", "type": "output"}],
                            "videos": [{"filename": "xxx.mp4", ...}]
                        }
                    },
                    "status": {
                        "status_str": "success" | "error",
                        "completed": true,
                        "messages": [...]   # 错误信息在这里
                    }
                }
            }
        """
        resp = await self._client.get(f"/history/{prompt_id}")
        resp.raise_for_status()
        data = resp.json()
        return data.get(prompt_id)  # 返回该 prompt_id 对应的记录，不存在则 None

    # ================================================================
    # 查询队列状态
    # ================================================================
    async def get_queue(self) -> dict[str, Any]:
        """
        GET /queue —— 查询当前 ComfyUI 队列。

        返回：
            {
                "QueueRunning": [{"prompt": [...], "prompt_id": "..."}],
                "QueuePending": [...]
            }
        """
        resp = await self._client.get("/queue")
        resp.raise_for_status()
        return resp.json()

    # ================================================================
    # 中断当前任务
    # ================================================================
    async def interrupt(self) -> None:
        """POST /interrupt —— 中断当前正在执行的任务。"""
        logger.warning("发送 interrupt 指令到 ComfyUI")
        resp = await self._client.post("/interrupt")
        resp.raise_for_status()

    # ================================================================
    # 清空队列
    # ================================================================
    async def clear_queue(self) -> None:
        """POST /queue —— 清空待执行队列（不影响正在运行的任务）。"""
        resp = await self._client.post("/queue", json={"delete": ["*"]})
        resp.raise_for_status()

    # ================================================================
    # 上传图片（图生视频 / ControlNet 输入用）
    # ================================================================
    async def upload_image(
        self,
        image_bytes: bytes,
        filename: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """
        POST /upload/image —— 上传图片到 ComfyUI input 目录。

        返回：
            {
                "name": "uploaded.png",
                "subfolder": "",
                "type": "input"
            }
        """
        files = {"image": (filename, image_bytes, "image/png")}
        data = {"overwrite": str(overwrite).lower()}
        resp = await self._client.post("/upload/image", files=files, data=data)
        resp.raise_for_status()
        return resp.json()

    # ================================================================
    # 健康检测 + 系统状态
    # ================================================================
    async def health_check(self) -> dict[str, Any]:
        """
        GET /system_stats —— 检测 ComfyUI 是否在线并获取系统状态。

        返回结构（关键部分）：
            {
                "system": {
                    "os": "nt",
                    "ram_total": 34359738368,
                    "comfyui_version": "0.0.55",
                    "python_version": "3.10.x"
                },
                "devices": [
                    {
                        "name": "NVIDIA GeForce RTX 4060",
                        "type": "cuda",
                        "vram_total": 8589934592,
                        "vram_free": 6442450944,
                        "torch_vram_total": ...,
                        "torch_vram_free": ...
                    }
                ]
            }

        如果 ComfyUI 未运行，httpx.ConnectError 会被上层捕获。
        """
        resp = await self._client.get("/system_stats")
        resp.raise_for_status()
        return resp.json()

    # ================================================================
    # 获取输出文件
    # ================================================================
    async def get_output_url(
        self,
        filename: str,
        subfolder: str = "",
        file_type: str = "output",
    ) -> str:
        """
        构建 ComfyUI 输出文件的访问 URL。

        ComfyUI 文件访问格式：
            GET /view?filename=xxx.gif&subfolder=&type=output
        """
        import urllib.parse
        params = urllib.parse.urlencode({
            "filename": filename,
            "subfolder": subfolder,
            "type": file_type,
        })
        return f"{self.base_url}/view?{params}"

    # ================================================================
    # 资源清理
    # ================================================================
    async def close(self) -> None:
        """关闭 HTTP 连接池。"""
        await self._client.aclose()


# 全局单例
comfyui_client = ComfyUIClient()
