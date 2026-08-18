"""
NexusVideo Backend - 文案化进度翻译器
============================================================
架构位置：core/progress_translator.py
被 core/comfyui_ws.py 调用（WebSocket 实时翻译），
被 routers/progress.py 调用（WebSocket 端点推送）。

白皮书 5.2 节 + 苏璃光设计系统规范 7.3 节：
    不显示百分比，用人类语言描述进度。
    四阶段文案库，每阶段随机选一条，同一条不连续重复。
    切换间隔：每 2.5-3 秒一条（由前端定时器控制），后端按需推送。

模块职责：
    1. 定义与苏璃光设计系统完全一致的文案库
    2. translate()：进度百分比 → 一条随机文案
    3. get_phase()：进度百分比 → 阶段号（1-4）
    4. get_estimated_text()：进度百分比 + 预计总时长 → 副文案
    5. _get_phase_messages()：当前阶段的所有可用文案（供前端做 crossfade 切换）
"""

import random
from typing import Optional


# ================================================================
# 四阶段文案库
# 来源：苏璃光《NexusVideo 设计系统规范》第 7.3 节
# 严禁修改文案内容，以确保前后端一致性。
# ================================================================

# 阶段一（0-20%）：理解与构思
PHASE_1_MESSAGES = [
    "正在构思画面…",
    "理解你的创意中…",
    "想象这个场景…",
    "正在解读你的描述…",
]

# 阶段二（20-60%）：绘制与渲染
PHASE_2_MESSAGES = [
    "正在绘制第一帧…",
    "画面逐渐成形…",
    "正在渲染细节…",
    "色彩与光影融合中…",
    "笔触正在落下…",
]

# 阶段三（60-90%）：动态化与优化
PHASE_3_MESSAGES = [
    "正在让画面动起来…",
    "优化流畅度中…",
    "即将完成，请稍候…",
    "画面正在鲜活起来…",
]

# 阶段四（90-100%）：收尾
PHASE_4_MESSAGES = [
    "最后润色中…",
    "马上就好…",
    "正在为您打包视频…",
]

# 阶段定义：(最小进度, 最大进度, 文案列表, 阶段名)
PHASES = [
    (0, 20, PHASE_1_MESSAGES, "构思"),
    (20, 60, PHASE_2_MESSAGES, "绘制"),
    (60, 90, PHASE_3_MESSAGES, "动态化"),
    (90, 100, PHASE_4_MESSAGES, "收尾"),
]

# 任务完成文案（100%）
COMPLETED_MESSAGE = "生成完成！"


# ================================================================
# 翻译器类
# 线程安全：每个 WebSocket 连接维护自己的 _last_message 状态，
# 不会跨连接共享。
# ================================================================
class ProgressTranslator:
    """
    文案化进度翻译器。

    使用方式（单连接场景）：
        translator = ProgressTranslator()
        msg = translator.translate(45.0)      # "正在渲染细节…"
        phase = translator.get_phase(45.0)    # 2
        est = translator.get_estimated_text(45.0, 120)  # "预计还需 66 秒"
    """

    def __init__(self) -> None:
        # 记录上一次返回的文案，避免连续重复（同一条不连续出现）
        self._last_message: Optional[str] = None

    # ================================================================
    # 核心翻译
    # ================================================================
    def translate(self, progress_pct: float) -> str:
        """
        将进度百分比翻译为一条随机的阶段文案。

        Args:
            progress_pct: 进度百分比（0-100）

        Returns:
            一条文案字符串。100% 时返回完成文案。
        """
        if progress_pct >= 100.0:
            return COMPLETED_MESSAGE

        phase_idx = self._resolve_phase_index(progress_pct)
        phase = PHASES[phase_idx]
        messages = phase[2]

        # 从阶段文案中随机选一条，确保不连续重复
        if len(messages) == 1:
            msg = messages[0]
        else:
            # 过滤掉上一条文案
            candidates = [m for m in messages if m != self._last_message]
            msg = random.choice(candidates) if candidates else random.choice(messages)

        self._last_message = msg
        return msg

    def get_phase(self, progress_pct: float) -> int:
        """
        返回当前进度对应的阶段号（1-4）。

        Args:
            progress_pct: 进度百分比（0-100）

        Returns:
            阶段号：1-4
        """
        return self._resolve_phase_index(progress_pct) + 1

    def get_estimated_text(self, progress_pct: float, total_time_seconds: int) -> str:
        """
        返回副文案（预计剩余时间）。

        文案规则（来源：设计系统规范 7.3 节补充说明）：
            - 无法估算：返回空字符串
            - 30 秒以上："预计还需 XX 秒"
            - 10-30 秒："预计还需 30 秒"
            - < 10 秒："马上就好"
            - 已完成：返回空字符串

        Args:
            progress_pct: 当前进度百分比（0-100）
            total_time_seconds: 预计总生成时间（秒）。如果无法估算，传 0 或负值。

        Returns:
            副文案字符串。
        """
        if progress_pct >= 100.0:
            return ""

        if total_time_seconds <= 0:
            return ""

        remaining_pct = max(0.0, 100.0 - progress_pct) / 100.0
        remaining_seconds = int(total_time_seconds * remaining_pct)

        if remaining_seconds < 10:
            return "马上就好"
        elif remaining_seconds <= 30:
            return "预计还需 30 秒"
        else:
            return f"预计还需 {remaining_seconds} 秒"

    def get_phase_messages(self, progress_pct: float) -> list[str]:
        """
        返回当前阶段的全部可用文案列表（供前端做 crossfade 切换）。

        前端收到此列表后，可在 2.5-3 秒间隔内自行轮换显示，
        降低后端推送频率。

        Args:
            progress_pct: 当前进度百分比

        Returns:
            当前阶段的全部文案列表
        """
        phase_idx = self._resolve_phase_index(progress_pct)
        return PHASES[phase_idx][2][:]  # 返回副本

    # ================================================================
    # 内部工具
    # ================================================================
    @staticmethod
    def _resolve_phase_index(progress_pct: float) -> int:
        """
        将进度百分比解析为阶段索引（0-3）。

        边界处理：
            - < 0 → 阶段 1
            - >= 100 → 阶段 4（不返回完成文案，由 translate() 单独处理）
            - 恰好等于阶段边界时归入下一阶段
        """
        clamped = max(0.0, min(99.999, progress_pct))
        for idx, (low, high, _, _) in enumerate(PHASES):
            if low <= clamped < high:
                return idx
        # 兜底：接近 100 时归入阶段 4
        return len(PHASES) - 1


# ================================================================
# 全局单例
# 注意：单例模式下 _last_message 状态会被共享。
# WebSocket 场景下，每个连接应创建独立实例以确保"不连续重复"
# 的正确语义。如需使用单例，请确保调用方不并发调用。
# ================================================================
progress_translator = ProgressTranslator()