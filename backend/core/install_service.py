"""
NexusVideo - 一键拉取引擎（M1 客户端拉取链路）
============================================================
职责：
  1. 读取 backend/install_manifest.json（拉取清单）与
     comfyui/extra_model_paths.yaml（模型根目录映射）。
  2. 按 manifest 顺序把 ComfyUI 源码 / 自定义节点 / 模型拉到
     settings.staging_dir（默认 D:/nexusvideo_staging）。
       - kind=file：HTTP Range **分段并行下载**（默认 8 段，见下）+ .tmp 分片
         + 流式合并 + 原子 os.replace；服务器不支持 Range 时自动回退单流续传。
       - kind=archive：HTTP 下载 zip 到 .tmp + 解压到目标目录（保持单流）。
  3. 单文件失败指数退避重试 3 次；主源失败自动回退 urls[1]/mirror。
  4. 进度通过内存事件总线广播，供 SSE（/install/progress）消费。
  5. verify()：按 required_models 检查模型是否齐全，返回缺失清单。

为什么要分段并行（真实踩坑）：
  单连接拉 6.7GB 模型跑不满带宽，且 VPN 一抖就抛
  `SSL: UNEXPECTED_EOF_WHILE_READING`，整文件从头重下。本机用 gopeed
  （多线程下载器）秒下同一文件，证明"多段并行 + 段级续传"是当前网络环境
  下唯一正解。故 kind=file 改为：
    - 能力探测 Range: bytes=0-0 → 206 + Content-Range 才走分段；
    - 段级失败只重下该段（失败粒度从整包降到 1/8 包）；
    - 段级混源：某段重试耗尽即切镜像源继续（offset 不变），全源皆败才算该段失败；
    - 断点续传靠 <name>.part.<i> 现有 size + <name>.meta.json 指纹，
      指纹不符（换源 / 总大小变 / 段数变）→ 丢弃全部 part 重来；
    - 任一段最终失败：只报该 item 失败，**已完成段原地保留**，重试只补差段。
  另：kind=file 目标文件"已存在且尺寸达标即跳过"，修掉 v0.2.12 每次重跑
  无脑重下 9.3GB 的问题（archive 类不做跳过：判断复杂、收益小）。

设计约束（来自产品硬性要求）：
  - 绝不在此机器真实下载大模型（验证只用本地小文件 / 本地 Range 服务）。
  - 运行期产物只落 D:（staging_dir），不写 C: 系统盘。
  - 进度文案不暴露百分比 / 节点名 / 显存。
  - SSE 事件 schema 只做**加字段**（bytes_done / bytes_total / skipped 等），
    前端 OneClickPull.vue 按现有字段消费，不可删改既有键。
"""

import asyncio
import json
import os
import re
import shutil
import threading
import time
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

# ---- 分段并行下载常量（kind=file 专用）----
MIN_SEGMENT_BYTES = 64 * 1024 * 1024  # 每段下限 64 MiB：小于此值不分段，避免碎段
PROBE_TIMEOUT = 15.0                  # Range 能力探测超时（bytes=0-0，几乎零流量）
SEGMENT_CONNECT_TIMEOUT = 30.0        # 单段连接超时
SEGMENT_READ_TIMEOUT = 120.0          # 单段读超时（比单流 600s 短：分段后单段数据量小）
SEGMENT_ATTEMPTS = 3                  # 段内重试次数（指数退避 1s/2s/4s，与整包旧口径一致）
PROGRESS_MIN_INTERVAL = 1.0           # 字节级进度事件最小间隔（秒），防止刷屏 SSE


class _SegmentedAborted(Exception):
    """分段模式中途发现服务器其实不理 Range（返回 200）→ 整体回退单流。"""


def _range_supported(status_code: int, content_range: str | None) -> bool:
    """仅当 206 + Content-Range 中总大小非 '*' 时，才认为服务器支持 Range 分段。"""
    if status_code != 206 or not content_range:
        return False
    m = re.search(r"bytes\s+\d+-\d+/(\d+|\*)", content_range, re.I)
    return bool(m and m.group(1) != "*")


def _parse_content_range_total(content_range: str | None) -> int | None:
    """从 Content-Range: bytes 0-0/6700000000 解析总长度。"""
    if not content_range:
        return None
    m = re.search(r"/(\d+)\s*$", content_range)
    return int(m.group(1)) if m else None


def _plan_segments(total: int, seg_max: int) -> list[tuple[int, int]]:
    """把 [0, total) 切成 seg_count 段左闭右闭区间。

    seg_count = min(seg_max, max(1, total // 64MiB))；余数摊给前 remainder 段，
    保证每段 ≥ 下限且并集严格覆盖全文件（合并后不会缺字节）。
    段数 ≤ 1 时返回空表，调用方回退单流。
    """
    if total <= 0:
        return []
    seg_count = max(1, min(int(seg_max or 1), total // MIN_SEGMENT_BYTES))
    if seg_count <= 1:
        return []
    base, remainder = divmod(total, seg_count)
    bounds: list[tuple[int, int]] = []
    start = 0
    for i in range(seg_count):
        end = start + base + (1 if i < remainder else 0) - 1
        bounds.append((start, end))
        start = end + 1
    return bounds


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
                self._bus.publish(
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
                await self._pull_item(
                    item, target_dir, tmp_dir, mirror, task_id=task_id, stage=stage,
                    item_index=done, item_total=total,
                )
            except Exception as e:
                logger.error("拉取失败 item=%s: %s", item.get("name"), e)
                self._bus.publish(
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
            self._bus.publish(
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
            self._bus.publish(
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
        self, item: dict, target_dir: Path, tmp_dir: Path, mirror: str | None,
        task_id: str | None = None, stage: str = "core", item_index: int = 0,
        item_total: int = 0,
    ) -> None:
        """单个 item：已存在即跳过 → 分段并行（file）→ 单流 fallback + 重试/回退。"""
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

        # ---- 已存在即跳过（仅 kind=file）----
        # v0.2.12 的坑：每次重跑 pull 会无脑重下 9.3GB。final 存在且尺寸落在
        # 容差内 → 视为已就位，发一条进度事件直接返回（verify 语义不变）。
        # archive 类不跳过：解压后与源包无尺寸对应关系，判断复杂、收益小。
        if kind != "archive":
            skip_bytes = await asyncio.to_thread(
                self._existing_ok_sync, final, expected, tol
            )
            if skip_bytes is not None:
                await self._emit(
                    stage,
                    f"「{item.get('name')}」已就位，跳过下载",
                    item_index, item_total, task_id,
                    skipped=True, bytes_done=skip_bytes, bytes_total=skip_bytes,
                )
                return

        # ---- 分段并行下载（仅 kind=file；archive 保持单流）----
        # 段级混源在 _download_segmented 内部完成（整表 candidates 传入），
        # 所以这里只需对"分段不可用"（不支持 Range / 总大小未知）回退单流。
        if kind != "archive" and ordered:
            try:
                ok = await self._download_segmented(
                    ordered, final, tmp_dir, expected, tol, task_id, stage,
                    item_index, item_total,
                )
                if ok:
                    return
                # 探测即发现不支持 Range / 文件太小 → 回退单流
            except _SegmentedAborted as e:
                # 下载中途才发现不理 Range：清掉分段中间产物后回退单流
                logger.info("Range 分段中止（%s），回退单流续传：%s", e, final.name)
                await asyncio.to_thread(self._purge_segment_artifacts, final, tmp_dir)
            except Exception as e:
                # 任一段最终失败：已完成段**保留**（下次重试只补差段），只报本 item
                raise RuntimeError(
                    f"下载失败：{item.get('name')}（分段下载未完成，"
                    f"已完成部分已保留，点重试继续）：{e}"
                ) from e

        last_err: Exception | None = None
        for url in ordered:
            for attempt in range(SEGMENT_ATTEMPTS):  # 指数退避重试 3 次
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
                    if attempt < SEGMENT_ATTEMPTS - 1:
                        await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s
        raise RuntimeError(f"下载失败：{item.get('name')} 所有源均不可用：{last_err}")

    # ============================================================
    # HTTP Range 分段并行下载（kind=file 主路径）
    # ============================================================
    async def _download_segmented(
        self,
        urls: list[str],
        final: Path,
        tmp_dir: Path,
        expected_size: int | None,
        tol_pct: float | None,
        task_id: str | None = None,
        stage: str = "core",
        item_index: int = 0,
        item_total: int = 0,
    ) -> bool:
        """分段并行下载一个文件。

        返回 True 表示"本路径已完成并落盘 final"；返回 False 表示
        "服务器不支持 Range / 总大小未知"——调用方应回退单流。
        抛 RuntimeError 表示"某段所有源都下不动"——已完成段保留在现场，
        下次重试从 .part.<i> + .meta.json 继续，只补差段。

        同步内核 asyncio.Semaphore(8) + asyncio.gather 调度，每段独立线程
        独立 httpx.Client（独立 TCP/TLS 连接），互不共享带宽以外的状态。
        """
        # 1) 能力探测：拿 total + 确认 Range 可用
        probe = await asyncio.to_thread(self._probe_range_support, urls)
        if probe is None:
            return False  # 全源都不支持 Range → 回退单流
        total, bounds = probe
        if not bounds:
            return False  # 文件太小（< 2 段下限）→ 单流更快

        meta_path = tmp_dir / (final.name + ".meta.json")
        seg_paths = [tmp_dir / f"{final.name}.part.{i}" for i in range(len(bounds))]

        # 2) meta 指纹：换源 / 总大小变 / 段数变 → 旧分片全部作废
        meta = {"urls": list(urls), "total_size": total, "seg_count": len(bounds)}
        if not self._meta_matches(meta_path, meta):
            await asyncio.to_thread(self._purge_segment_artifacts, final, tmp_dir)
            await asyncio.to_thread(self._write_meta, meta_path, meta)
        else:
            # 断点续传护栏：分片不得超出所属段区间（手工塞过长的 part 文件
            # 或上次写坏 → 该段作废重下，绝不产生错位数据）
            await asyncio.to_thread(
                self._trim_stale_parts, seg_paths, bounds, meta_path, meta
            )

        # 3) 段级并发调度（每段一个协程，Semaphore 限 N 路，N=download_segments）
        sem = asyncio.Semaphore(max(1, int(settings.download_segments) or 8))
        # 进度基数：续传时已完成段/半截段的现存字节先计入，避免进度从 0 跳变
        seeded = sum(
            min(p.stat().st_size, b[1] - b[0] + 1) if p.exists() else 0
            for p, b in zip(seg_paths, bounds)
        )
        counters = {"done": seeded}
        lock = threading.Lock()
        last_emit = [time.monotonic()]

        async def _watchdog() -> None:
            """字节级进度节流广播：不新增 SSE 事件类型，仅沿用 _emit 节奏。"""
            while True:
                await asyncio.sleep(PROGRESS_MIN_INTERVAL)
                done_bytes = counters["done"]
                if done_bytes and time.monotonic() - last_emit[0] >= PROGRESS_MIN_INTERVAL:
                    last_emit[0] = time.monotonic()
                    await self._emit(
                        stage, STAGE_TEXT.get(stage, ""), item_index, item_total, task_id,
                        bytes_done=min(done_bytes, total), bytes_total=total,
                    )

        watchdog = asyncio.create_task(_watchdog())
        try:
            results = await asyncio.gather(
                *[
                    self._download_one_segment(
                        sem, urls, i, bounds[i], seg_paths[i], counters, lock
                    )
                    for i in range(len(bounds))
                ],
                return_exceptions=True,
            )
        finally:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)

        # 4) 段级结果归并：
        #    - 有段报"服务器不理 Range" → 上抛 _SegmentedAborted（调用方回退单流）
        #    - 其他任一失败 → 保留现场抛错（不动 final，重试只补差段）
        for r in results:
            if isinstance(r, _SegmentedAborted):
                raise r
        failed = [i for i, r in enumerate(results) if isinstance(r, BaseException)]
        if failed:
            first_err = results[failed[0]]
            raise RuntimeError(
                f"{len(failed)}/{len(bounds)} 个分段下载失败（段 {failed[:5]}），"
                f"已完成段已保留：{first_err}"
            )

        # 5) 流式合并 → .merging → 校验 → 原子 os.replace
        await asyncio.to_thread(
            self._merge_segments_sync, final, tmp_dir, seg_paths, bounds, total,
            expected_size, tol_pct,
        )
        await self._emit(
            stage, STAGE_TEXT.get(stage, ""), item_index + 1, item_total, task_id,
            bytes_done=total, bytes_total=total,
        )
        return True

    async def _download_one_segment(
        self, sem, urls, index, bounds, part_path, counters, lock
    ) -> dict:
        """单段：Semaphore 排队 → 段内 3 次指数退避 → 失败即轮换下一源。

        混源规则（设计定稿第 6 条）：段 offset 恒定，源 A 重试耗尽换源 B 从
        该段断点继续；所有源都失败才算该段失败。
        """
        low, high = bounds
        last_err: Exception | None = None
        aborted: _SegmentedAborted | None = None
        async with sem:
            for url in urls:
                for attempt in range(SEGMENT_ATTEMPTS):
                    try:
                        added = await asyncio.to_thread(
                            self._fetch_segment_sync, url, index, low, high, part_path
                        )
                        with lock:
                            counters["done"] += added
                        return {"segment": index, "source": url}
                    except _SegmentedAborted as e:
                        # 该源实际不理 Range（探测 206 / 真下 200）：换下一候选源，
                        # 重试同一个源没有意义，故直接 break 出去。
                        aborted = e
                        break
                    except Exception as e:
                        last_err = e
                        if attempt < SEGMENT_ATTEMPTS - 1:
                            await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s
        # 所有候选源对本段都不可用：优先透出"不理 Range"信号（触发整体回退单流）
        if aborted is not None:
            raise aborted
        raise RuntimeError(f"segment {index} 全源失败: {last_err}")

    def _probe_range_support(self, urls: list[str]) -> tuple[int, list[tuple[int, int]]] | None:
        """对候选源逐个发 `Range: bytes=0-0` 试请求（几乎零流量）。

        返回 (total_size, bounds)；任一源返回 206 + 可解析 Content-Range 即
        认为可分段；全源皆 200/无 Content-Range/网络错 → None（回退单流）。
        """
        seg_max = max(1, int(settings.download_segments) or 8)
        for url in urls:
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(PROBE_TIMEOUT, read=PROBE_TIMEOUT),
                    follow_redirects=True,
                ) as client:
                    with client.stream("GET", url, headers={"Range": "bytes=0-0"}) as resp:
                        cr = resp.headers.get("Content-Range")
                        status = resp.status_code
                        total = _parse_content_range_total(cr)
                        if total is None and status == 200:
                            # 200 时也可能带可靠 Content-Length
                            cl = resp.headers.get("Content-Length")
                            total = int(cl) if cl and cl.isdigit() else None
                        if status == 200:
                            logger.info("源不支持 Range（200），尝试下一候选源：%s", url)
                            continue
                        resp.raise_for_status()
                        if not _range_supported(status, cr) or not total:
                            continue
                        return total, _plan_segments(total, seg_max)
            except Exception as e:
                logger.warning("Range 探测异常（%s）：%s", url, e)
                continue
        return None

    def _fetch_segment_sync(
        self, url: str, index: int, low: int, high: int, part_path: Path
    ) -> int:
        """同步下载线程：把段 [low, high] 补完到 part_path，返回本次新增字节数。

        断点续传：段起始 offset = part 现有 size（夹到区间内）。
        写盘 `"ab"`（续传）/ `"xb"`（首建，独占创建防并发抢写）。
        段满即返回——服务器多发（不精确 Range）也在此截断丢弃。
        """
        start = part_path.stat().st_size if part_path.exists() else 0
        seg_len = high - low + 1
        if start > seg_len:
            # 脏分片（超长）→ 作废重下
            part_path.unlink(missing_ok=True)
            start = 0
        if start == seg_len:
            return 0  # 该段已完成，跳过
        offset = low + start
        headers = {"Range": f"bytes={offset}-{high}"}
        written = 0
        with httpx.Client(
            timeout=httpx.Timeout(SEGMENT_CONNECT_TIMEOUT, read=SEGMENT_READ_TIMEOUT),
            follow_redirects=True,
        ) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code == 416:
                    # 段已满（服务器认为区间越界）→ 视为完成
                    return 0
                if resp.status_code == 200:
                    # 探测说支持、真下又不理 Range（代理/LB 行为不一致）
                    raise _SegmentedAborted(
                        f"segment {index}: 服务器忽略 Range（HTTP 200）"
                    )
                if resp.status_code != 206:
                    resp.raise_for_status()
                    raise IOError(
                        f"segment {index}: 期望 206，实际 HTTP {resp.status_code}"
                    )
                # 状态确认可用后才建/开分片：失败请求不留 0 字节垃圾 part
                with open(part_path, "ab") as f:  # 纯追加（含 0 字节新建）
                    for chunk in resp.iter_bytes(CHUNK):
                        if not chunk:
                            continue
                        room = seg_len - start - written
                        if room <= 0:
                            break
                        if len(chunk) > room:
                            chunk = chunk[:room]  # 不精确 Range 防越界
                        f.write(chunk)
                        written += len(chunk)
        if start + written < seg_len:
            raise IOError(
                f"segment {index}: 连接提前结束（{start + written}/{seg_len}）"
            )
        return written

    def _merge_segments_sync(
        self,
        final: Path,
        tmp_dir: Path,
        seg_paths: list[Path],
        bounds: list[tuple[int, int]],
        total: int,
        expected_size: int | None,
        tol_pct: float | None,
    ) -> dict:
        """按段号顺序流式拼接（1 MiB copyfileobj）→ 校验 → os.replace 到 final。

        全程写 <name>.merging，成功前 final 永不出半截；
        只有合并 + 校验全通过才 unlink 各 part 与 meta
        （任何一步失败现场都保留，下次重试只补差段）。
        """
        final.parent.mkdir(parents=True, exist_ok=True)
        merging = tmp_dir / (final.name + ".merging")
        with open(merging, "wb") as out:
            for i, (p, (low, high)) in enumerate(zip(seg_paths, bounds)):
                want = high - low + 1
                have = p.stat().st_size if p.exists() else -1
                if have != want:
                    merging.unlink(missing_ok=True)
                    raise IOError(
                        f"分段 {i} 不完整（{have}/{want} bytes）：{p.name}"
                    )
                with open(p, "rb") as f:
                    shutil.copyfileobj(f, out, CHUNK)

        # 总大小校验（先严格核对拼接完整性，再套 manifest 声明的容差）
        merged_size = merging.stat().st_size
        if merged_size != total:
            merging.unlink(missing_ok=True)
            raise ValueError(
                f"合并大小异常 {final.name}: got {merged_size}, expect {total}"
            )
        if expected_size:
            tol = max(int(expected_size * (tol_pct or 0.0)), 1024)
            if abs(merged_size - expected_size) > tol:
                merging.unlink(missing_ok=True)
                # 远端总大小与声明不符 → 这些分片没有复用价值，整件重来
                self._purge_segment_artifacts(final, tmp_dir)
                raise ValueError(
                    f"size mismatch {final.name}: got {merged_size}, "
                    f"expected ~{expected_size}"
                )

        os.replace(merging, final)  # 原子重命名：永不出现半截 final
        for p in seg_paths:
            p.unlink(missing_ok=True)
        (tmp_dir / (final.name + ".meta.json")).unlink(missing_ok=True)
        return {"merged_bytes": merged_size, "final": str(final)}

    def _existing_ok_sync(
        self, final: Path, expected_size: int | None, tol_pct: float | None
    ) -> int | None:
        """final 已存在且尺寸落在 expected±tol 内 → 返回其字节数；否则 None。

        必须有 size_bytes 才允许跳过：无声明尺寸时无法区分"完整文件"与
        "上次中断的截断文件"，宁可不跳（下载路径本身有续传，代价可控）。
        """
        try:
            if not expected_size:
                return None
            if not final.exists() or not final.is_file():
                return None
            size = final.stat().st_size
            tol = max(int(expected_size * (tol_pct or 0.0)), 1024)
            if abs(size - expected_size) > tol:
                return None
            return size
        except OSError:
            return None

    @staticmethod
    def _meta_matches(meta_path: Path, meta: dict) -> bool:
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            return (
                isinstance(old, dict)
                and old.get("urls") == meta["urls"]
                and old.get("total_size") == meta["total_size"]
                and old.get("seg_count") == meta["seg_count"]
            )
        except Exception:
            return False

    @staticmethod
    def _write_meta(meta_path: Path, meta: dict) -> None:
        tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp, meta_path)

    @staticmethod
    def _purge_segment_artifacts(final: Path, tmp_dir: Path) -> None:
        """丢弃某文件的全部分段中间产物（part.<i> / meta / merging），不动 final。"""
        if not tmp_dir.exists():
            return
        prefix = final.name + ".part."
        for p in tmp_dir.iterdir():
            if p.name.startswith(prefix) or p.name == final.name + ".meta.json" \
                    or p.name == final.name + ".merging":
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

    def _trim_stale_parts(
        self, seg_paths, bounds, meta_path: Path, meta: dict
    ) -> None:
        """续传护栏：任何超出自身段区间的脏分片直接作废重下。"""
        changed = False
        for p, (low, high) in zip(seg_paths, bounds):
            try:
                if p.exists() and p.stat().st_size > (high - low + 1):
                    p.unlink(missing_ok=True)
                    changed = True
            except OSError:
                continue
        if changed:
            self._write_meta(meta_path, meta)

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

    async def _emit(self, stage, message, current, total, task_id=None, **extra) -> None:
        """广播一帧进度。

        既有键（stage/message/current/total/task_id）是前端 OneClickPull.vue
        的消费契约，**永不删改**；分段下载新增的字节级字段（bytes_done /
        bytes_total / skipped）只通过 **extra 追加**，老前端按未知键忽略即可。
        """
        event = {
            "stage": stage,
            "message": message,
            "current": current,
            "total": total,
            "task_id": task_id,
        }
        event.update(extra)
        self._bus.publish(event)


# 模块级单例（路由复用）。构造只读 manifest/yaml，失败不阻塞后端启动。
try:
    install_service = InstallService()
except Exception as _e:  # pragma: no cover
    install_service = None
    logger.warning("InstallService 初始化失败（/install 将返回 503）：%s", _e)
