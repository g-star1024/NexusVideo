"""
NexusVideo - 一键拉取引擎（M1 客户端拉取链路）
============================================================
职责：
  1. 读取 backend/install_manifest.json（拉取清单）与
     comfyui/extra_model_paths.yaml（模型根目录映射）。
  2. 按 manifest 顺序把 ComfyUI 源码 / 自定义节点 / 模型拉到
     settings.staging_dir（默认 D:/nexusvideo_staging）。
       - kind=file：HTTP Range 续传 + .tmp 临时文件 + 原子 os.replace。
       - kind=archive：HTTP 下载 zip 到 .tmp + 解压到目标目录。
  3. 单文件失败指数退避重试 3 次；主源失败自动回退 urls[1]/mirror。
  4. 进度通过内存事件总线广播，供 SSE（/install/progress）消费。
  5. verify()：按 required_models 检查模型是否齐全，返回缺失清单。

设计约束（来自产品硬性要求）：
  - 绝不在此机器真实下载大模型（验证只用本地小文件 / 本地 Range 服务）。
  - 运行期产物只落 D:（staging_dir），不写 C: 系统盘。
  - 进度文案不暴露百分比 / 节点名 / 显存。
"""

import asyncio
import json
import os
import shutil
import uuid
import zipfile
from pathlib import Path

import httpx
import yaml
from loguru import logger

from config import settings

# ---- 路径定位 ----
BACKEND_DIR = Path(__file__).resolve().parent.parent          # .../backend
PROJECT_ROOT = BACKEND_DIR.parent                             # 项目根
DEFAULT_MANIFEST = BACKEND_DIR / "install_manifest.json"
DEFAULT_EXTRA_YAML = PROJECT_ROOT / "comfyui" / "extra_model_paths.yaml"

# ---- 进度文案（设计文档口径，不暴露技术细节）----
STAGE_TEXT = {
    "prepare": "正在准备运行环境…",
    "core": "正在下载核心程序…",
    "model": "正在配置模型，请稍候…",
    "verify": "即将完成，马上可以出片",
}
FAIL_TEXT = "网络不太顺，已停在这一步，点重试继续（不会重新下载已完成部分）"

CHUNK = 1 << 20  # 1 MiB


class _ProgressBus:
    """极简内存事件总线：pull 生产事件，SSE 消费者订阅。"""

    def __init__(self) -> None:
        self._subs: list[asyncio.Queue] = []
        self._last: dict | None = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.append(q)
        if self._last is not None:
            q.put_nowait(self._last)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subs.remove(q)
        except ValueError:
            pass

    def publish(self, event: dict) -> None:
        self._last = event
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


class InstallService:
    """manifest 驱动的拉取引擎 + 自检。"""

    def __init__(
        self,
        manifest_path: str | Path | None = None,
        extra_yaml_path: str | Path | None = None,
    ) -> None:
        self.manifest_path = Path(manifest_path or DEFAULT_MANIFEST)
        self.extra_yaml_path = Path(extra_yaml_path or DEFAULT_EXTRA_YAML)
        self.manifest = self._load_manifest()
        self.model_base_path = self._load_model_base_path()
        self._bus = _ProgressBus()
        self._pull_lock = asyncio.Lock()

    # ============================================================
    # 加载
    # ============================================================
    def _load_manifest(self) -> dict:
        with open(self.manifest_path, "r", encoding="utf-8-sig") as f:
            return json.load(f)

    def _load_model_base_path(self) -> str:
        """从 extra_model_paths.yaml 取 base_path（模型根目录）。"""
        try:
            with open(self.extra_yaml_path, "r", encoding="utf-8-sig") as f:
                data = yaml.safe_load(f) or {}
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, dict) and v.get("base_path"):
                        return str(v["base_path"])
        except Exception as e:  # pragma: no cover - 兜底
            logger.warning("读取 extra_model_paths.yaml 失败，回退到 staging/models：%s", e)
        # 兜底：staging_dir/models
        return str(Path(settings.staging_dir) / "models")

    # ============================================================
    # 拉取
    # ============================================================
    async def pull(
        self, target_dir: str | Path, mirror: str | None = None, task_id: str | None = None
    ) -> str:
        """
        按 manifest items 顺序拉取全部资产。
        失败时不删除已完成部分（重试可续传）。
        返回 task_id（调用方用于关联进度流）。
        """
        task_id = task_id or uuid.uuid4().hex
        async with self._pull_lock:
            try:
                await self._pull_impl(Path(target_dir), mirror, task_id)
            except Exception as e:  # 失败态广播，供前端提示"点重试继续"
                await self._bus.publish(
                    {
                        "stage": "failed",
                        "message": FAIL_TEXT,
                        "task_id": task_id,
                        "error": str(e),
                        "failed": True,
                    }
                )
                raise
        return task_id

    async def _pull_impl(self, target_dir: Path, mirror: str | None, task_id: str) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = target_dir / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        items = self.manifest.get("items", [])
        total = len(items)
        done = 0

        await self._emit("prepare", STAGE_TEXT["prepare"], 0, total, task_id)
        for item in items:
            stage = item.get("stage", "core")
            await self._emit(stage, STAGE_TEXT.get(stage, ""), done, total, task_id)
            try:
                await self._pull_item(item, target_dir, tmp_dir, mirror)
            except Exception as e:
                logger.error("拉取失败 item=%s: %s", item.get("name"), e)
                await self._bus.publish(
                    {
                        "stage": "failed",
                        "message": FAIL_TEXT,
                        "task_id": task_id,
                        "error": str(e),
                        "failed": True,
                        "current": done,
                        "total": total,
                    }
                )
                raise
            done += 1

        # 拉完自检
        missing = await self.verify(self.model_base_path)
        if missing:
            await self._bus.publish(
                {
                    "stage": "verify",
                    "message": "组件已就位，但部分模型自检未通过：" + ", ".join(missing),
                    "task_id": task_id,
                    "warning": True,
                    "missing": missing,
                    "current": total,
                    "total": total,
                }
            )
        else:
            await self._bus.publish(
                {
                    "stage": "verify",
                    "message": STAGE_TEXT["verify"],
                    "task_id": task_id,
                    "ok": True,
                    "done": True,
                    "current": total,
                    "total": total,
                }
            )

    async def _pull_item(
        self, item: dict, target_dir: Path, tmp_dir: Path, mirror: str | None
    ) -> None:
        """单个 item：组装候选源（主源 + mirror + urls[1]），重试 + 回退。"""
        kind = item.get("kind", "file")
        final = target_dir / item["target"]

        urls = list(item.get("urls", []))
        # prefer_env：运行时用 settings 里的覆盖源（如 comfyui_tarball_url）
        prefer = item.get("prefer_env")
        if prefer:
            env_val = getattr(settings, prefer, None)
            if env_val:
                urls = [env_val] + urls

        candidates: list[str] = []
        if urls:
            candidates.append(urls[0])
        if mirror:
            candidates.append(mirror)
        candidates.extend(urls[1:])
        # 去重保序
        seen: set[str] = set()
        ordered = [u for u in candidates if u and u not in seen and not seen.add(u)]

        expected = item.get("size_bytes")
        tol = item.get("size_tolerance_pct")

        last_err: Exception | None = None
        for url in ordered:
            for attempt in range(3):  # 指数退避重试 3 次
                try:
                    if kind == "archive":
                        await asyncio.to_thread(
                            self._download_archive_sync, url, final, tmp_dir, expected, tol
                        )
                    else:
                        await asyncio.to_thread(
                            self._download_file_sync, url, final, tmp_dir, expected, tol
                        )
                    return
                except Exception as e:
                    last_err = e
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s
        raise RuntimeError(f"下载失败：{item.get('name')} 所有源均不可用：{last_err}")

    # ============================================================
    # 下载内核（同步，跑在 asyncio.to_thread 中，避免阻塞事件循环）
    # ============================================================
    def _stream_to_file(
        self,
        url: str,
        final_path: str | Path,
        tmp_dir: str | Path,
        expected_size: int | None,
        tol_pct: float | None,
    ) -> dict:
        """
        下载单文件：写到 <tmp_dir>/<name>.part，完成后原子 os.replace 到 final。
        支持 HTTP Range 断点续传（已有 .part 则从断点字节继续）。
        返回 {'resumed_from': int, 'final': str} 供测试/诊断。
        """
        final = Path(final_path)
        final.parent.mkdir(parents=True, exist_ok=True)
        part = Path(tmp_dir) / (final.name + ".part")
        Path(tmp_dir).mkdir(parents=True, exist_ok=True)  # 确保临时目录存在

        start = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={start}-"} if start > 0 else {}

        with httpx.Client(
            timeout=httpx.Timeout(30.0, read=600.0), follow_redirects=True
        ) as client:
            with client.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                # 服务器忽略 Range（返回 200 而非 206）→ 从头重下
                if start > 0 and resp.status_code == 200:
                    part.unlink(missing_ok=True)
                    start = 0
                mode = "ab" if start > 0 else "wb"
                with open(part, mode) as f:
                    for chunk in resp.iter_bytes(CHUNK):
                        if chunk:
                            f.write(chunk)

        # size 校验（带容差，避免轻微大小差异误报）
        if expected_size:
            actual = part.stat().st_size
            tol = max(int(expected_size * (tol_pct or 0.0)), 1024)
            if abs(actual - expected_size) > tol:
                part.unlink(missing_ok=True)
                raise ValueError(
                    f"size mismatch {final.name}: got {actual}, expected ~{expected_size}"
                )

        os.replace(part, final)  # 原子重命名
        return {"resumed_from": start, "final": str(final)}

    def _download_file_sync(self, url, final_path, tmp_dir, expected_size, tol_pct) -> dict:
        return self._stream_to_file(url, final_path, tmp_dir, expected_size, tol_pct)

    def _download_archive_sync(
        self, url, final_dir, tmp_dir, expected_size, tol_pct
    ) -> dict:
        """下载 zip 到 .tmp，再解压到 final_dir（处理顶层单目录扁平化）。"""
        final_dir = Path(final_dir)
        zip_final = Path(tmp_dir) / (final_dir.name + ".zip")
        stats = self._stream_to_file(url, zip_final, tmp_dir, expected_size, tol_pct)
        self._extract_archive(zip_final, final_dir)
        zip_final.unlink(missing_ok=True)
        return stats

    def _extract_archive(self, zip_path: Path, target_dir: Path) -> None:
        target_dir = Path(target_dir)
        work = target_dir.parent / (target_dir.name + ".extract")
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(work)
        entries = [p for p in work.iterdir()]
        src = work
        if len(entries) == 1 and entries[0].is_dir():
            src = entries[0]
        shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(target_dir))
        shutil.rmtree(work, ignore_errors=True)

    # ============================================================
    # 自检
    # ============================================================
    async def verify(self, target_dir: str | Path) -> list[str]:
        """
        按 required_models 检查模型齐全度。
        target_dir 既可以是模型根（base_path），也可以是 staging 根
        （会自动补 models/ 再查）。返回缺失的相对路径清单，空列表=通过。
        """
        root = Path(target_dir)
        missing: list[str] = []
        for rel in self.manifest.get("required_models", []):
            cand1 = root / rel
            cand2 = root / "models" / rel
            if not (cand1.exists() or cand2.exists()):
                missing.append(rel)
        return missing

    # ============================================================
    # 进度流（SSE）
    # ============================================================
    async def progress_stream(self):
        """逐条 yield 进度事件，直到 done/failed 终止。"""
        q = self._bus.subscribe()
        try:
            while True:
                event = await q.get()
                yield event
                if event.get("done") or event.get("failed"):
                    break
        finally:
            self._bus.unsubscribe(q)

    async def _emit(self, stage, message, current, total, task_id=None) -> None:
        await self._bus.publish(
            {
                "stage": stage,
                "message": message,
                "current": current,
                "total": total,
                "task_id": task_id,
            }
        )


# 模块级单例（路由复用）。构造只读 manifest/yaml，失败不阻塞后端启动。
try:
    install_service = InstallService()
except Exception as _e:  # pragma: no cover
    install_service = None
    logger.warning("InstallService 初始化失败（/install 将返回 503）：%s", _e)
