"""
NexusVideo Backend - 工作流翻译官（核心胶水代码）v2
============================================================
这是整个项目的"灵魂模块"——白皮书 4.2 节的"翻译官"。

v2 更新：
  1. 三大模式完整模板支持（txt2video / img2video / video2video）
  2. 运动强度滑块 → denoising_strength 映射
  3. 视频风格化 3 套风格预设 prompt 模板（油画 / 3D / 水墨）
  4. 模板使用字符串替换（{{placeholder}} 语法）+ 字段映射双重机制
  5. 显存降级链（OOM 时自动降级）
  6. 模板缓存（避免每次读磁盘）

白皮书参考代码：
    workflow["6"]["inputs"]["text"] = request["prompt"]
    workflow["8"]["inputs"]["seed"] = random.randint(1, 1000000)

模板 JSON 格式：
    工作流模板使用 {{placeholder_name}} 占位符语法。
    翻译官会先将占位符替换为实际值，再执行字段映射表的精确替换。
"""

import copy
import json
import math
import re
import random
from pathlib import Path
from typing import Any

from loguru import logger

from config import settings
from models.schemas import GenerationMode, GenerateRequest
from exceptions import (
    WorkflowNotFoundError,
    WorkflowTemplateError,
    InvalidInputError,
)


# ================================================================
# 字段映射表
# ================================================================
# 声明式定义"前端参数 → ComfyUI 工作流节点字段"的映射关系。
# 格式: { 模板名: { 模式: { 节点ID: { "inputs": { comfyui_field: param_name } } } } }
WORKFLOW_FIELD_MAP: dict[str, dict[GenerationMode, dict[str, dict[str, dict[str, str]]]]] = {
    "txt2video": {
        GenerationMode.TXT2VIDEO: {
            # Wan2.1 T2V 工作流节点映射
            "1": {"inputs": {"text": "prompt"}},          # CLIPTextEncode 正向
            "3": {"inputs": {"text": "_negative_prompt"}}, # CLIPTextEncode 负向
            "5": {"inputs": {                            # EmptyLatentImage 分辨率/帧数
                "width": "width",
                "height": "height",
                "length": "frames",
            }},
            "6": {"inputs": {                            # KSampler 核心采样参数
                "seed": "seed",
                "steps": "steps",
                "cfg": "cfg",
            }},
        }
    },
    "img2video": {
        GenerationMode.IMG2VIDEO: {
            # AnimateDiff + ControlNet 图生视频
            "1": {"inputs": {"text": "prompt"}},
            "2": {"inputs": {"text": "_negative_prompt"}},
            "11": {"inputs": {                          # AnimateDiffSampler
                "seed": "seed",
                "steps": "steps",
                "cfg": "cfg",
                "denoise": "denoising_strength",
                "context_length": "_context_length",
            }},
            "5": {"inputs": {"strength": "_controlnet_strength"}},
        }
    },
    "video2video": {
        GenerationMode.VIDEO2VIDEO: {
            # 单 ControlNet Depth + AnimateDiff 视频风格化
            "1": {"inputs": {"text": "_final_prompt"}},
            "2": {"inputs": {"text": "_final_negative"}},
            "14": {"inputs": {                          # KSampler
                "seed": "seed",
                "steps": "steps",
                "cfg": "cfg",
                "denoise": "denoising_strength",
            }},
            "5": {"inputs": {"strength": "_controlnet_strength"}},
        }
    },
}


# ================================================================
# 默认负向提示词
# ================================================================
DEFAULT_NEGATIVE_PROMPTS: dict[GenerationMode, str] = {
    GenerationMode.TXT2VIDEO: (
        "text, watermark, low quality, blurry, deformed, "
        "distorted, bad anatomy, extra limbs, lowres, "
        "worst quality, jpeg artifacts, duplicate"
    ),
    GenerationMode.IMG2VIDEO: (
        "text, watermark, low quality, blurry, deformed, "
        "morphing, flickering, distorted"
    ),
    GenerationMode.VIDEO2VIDEO: (
        "text, watermark, low quality, blurry, color shift, "
        "artifacts"
    ),
}


# ================================================================
# 视频风格化：3 套风格预设 prompt 模板
# ================================================================
# 每套预设包含：正向 prompt 模板、负向 prompt、CFG、denoising、ControlNet strength
STYLE_PRESETS: dict[str, dict] = {
    "oil": {
        "positive": (
            "{user_desc}, oil painting style, thick visible brushstrokes, "
            "rich textured canvas, classical fine art, baroque lighting, "
            "masterpiece, gallery quality, 8k"
        ),
        "negative": (
            "photo, realistic, 3d render, digital art, flat colors, "
            "blurry, low quality, watermark"
        ),
        "denoising": 0.55,
        "cfg": 7.0,
        "controlnet_strength": 0.75,
    },
    "3d": {
        "positive": (
            "{user_desc}, 3d pixar animation style, octane render, "
            "soft studio lighting, vibrant saturated colors, "
            "stylized characters, smooth subsurface scattering, 8k"
        ),
        "negative": (
            "photo, realistic, dark, gritty, 2d flat, sketch, "
            "low quality, watermark"
        ),
        "denoising": 0.60,
        "cfg": 7.5,
        "controlnet_strength": 0.75,
    },
    "ink": {
        "positive": (
            "{user_desc}, traditional chinese ink wash painting, "
            "sumi-e, minimalist brush strokes, monochrome with subtle "
            "color accents, xuan paper texture, negative space, zen aesthetic"
        ),
        "negative": (
            "photo, realistic, 3d, vibrant saturated colors, digital art, "
            "busy background, watermark"
        ),
        "denoising": 0.55,
        "cfg": 6.5,
        "controlnet_strength": 0.75,
    },
}


# ================================================================
# 运动强度 → denoising_strength 映射
# ================================================================
# 映射公式（来自甄知远 Task #4 报告）：
#   denoising = 0.30 + (motion_strength / 10) × 0.60
# 硬上限 0.90，避免过度改写原图/原视频
#
# motion_strength=1  → denoise=0.36  (微动)
# motion_strength=5  → denoise=0.60  (明显运动，默认档)
# motion_strength=10 → denoise=0.90  (剧烈运动)
MOTION_STRENGTH_MIN = 0.30
MOTION_STRENGTH_COEFF = 0.60
MOTION_STRENGTH_MAX = 0.90
MOTION_STRENGTH_DEFAULT = 5  # 默认档位


def motion_strength_to_denoising(strength: int) -> float:
    """
    将运动强度滑块值（1-10）映射为 denoising_strength。

    参数：
        strength: 运动强度，范围 1-10

    返回：
        denoising_strength，范围 0.36-0.90

    公式：
        denoising = 0.30 + (strength / 10) × 0.60
        硬上限 0.90
    """
    if strength < 1:
        strength = 1
    elif strength > 10:
        strength = 10

    denoise = MOTION_STRENGTH_MIN + (strength / 10.0) * MOTION_STRENGTH_COEFF

    # 硬上限
    denoise = min(denoise, MOTION_STRENGTH_MAX)

    return round(denoise, 4)


# ================================================================
# 工作流翻译官
# ================================================================
class WorkflowTranslator:
    """
    工作流翻译官：前端简化参数 → ComfyUI API JSON。

    核心方法：
        translate(request) → (workflow_dict, resolved_seed)
    """

    def __init__(self):
        # 模板缓存：{模板文件名: 原始JSON字符串}
        # 缓存原始字符串而非解析后的 dict，保证每次深拷贝都是纯净的
        self._template_cache: dict[str, str] = {}

    # ================================================================
    # 模板加载（带缓存）
    # ================================================================
    def _load_template(self, mode: GenerationMode) -> dict[str, Any]:
        """
        从 workflows/ 目录加载对应模式的工作流模板 JSON。
        首次加载后缓存原始字符串，后续直接从缓存深拷贝。

        模板文件命名规则：{mode.value}.json
            txt2video.json / img2video.json / video2video.json
        """
        template_name = f"{mode.value}.json"
        template_path: Path = settings.workflows_dir / template_name

        if template_name not in self._template_cache:
            if not template_path.exists():
                raise WorkflowNotFoundError(template_name)

            try:
                with open(template_path, "r", encoding="utf-8") as f:
                    raw = f.read()
                # 验证 JSON 合法性
                json.loads(raw)
                self._template_cache[template_name] = raw
                logger.info(f"已加载工作流模板：{template_name}")
            except json.JSONDecodeError as e:
                raise WorkflowTemplateError(f"JSON 解析失败：{e}")
            except Exception as e:
                raise WorkflowTemplateError(f"模板加载失败：{e}")

        # 每次返回新的深拷贝
        return json.loads(self._template_cache[template_name])

    # ================================================================
    # 随机种子管理
    # ================================================================
    @staticmethod
    def _resolve_seed(seed: int | None) -> int:
        """
        随机种子管理：
          - None → 自动生成 [0, 2^32-1]
          - 固定值 → 直接使用该值（可复现）
        """
        if seed is None:
            return random.randint(0, 2**32 - 1)
        return seed

    # ================================================================
    # 模板占位符替换
    # ================================================================
    @staticmethod
    def _replace_placeholders(
        workflow_str: str,
        params: dict[str, Any],
    ) -> str:
        """
        将 JSON 字符串中的占位符替换为实际值。

        占位符语法：
          - {{prompt}}           → 字符串（模板中已带引号，只替换引号内的内容）
          - __INT__seed          → 整数（模板中已带引号，替换为不带引号的数字）
          - __FLOAT__cfg         → 浮点数（模板中已带引号，替换为不带引号的数字）
          - __BOOL__flag         → 布尔值

        模板示例：
          "text": "{{prompt}}"       → 替换后: "text": "cyberpunk cat"
          "seed": "__INT__seed"      → 替换后: "seed": 42
          "cfg": "__FLOAT__cfg"      → 替换后: "cfg": 5.0

        注意：{{prompt}} 的引号是模板 JSON 的字符串边界，
              替换时只替换引号内的 {{prompt}} 部分，不额外加引号。
        """
        # 先处理 __TYPE__name 格式（类型明确的数值占位符）
        # 模板中："__INT__seed" → 替换后：42（去掉外层引号）
        # 所以我们需要匹配带引号的整段并替换
        def type_replacer(match: re.Match) -> str:
            var_type = match.group(1)
            name = match.group(2)
            value = params.get(name)
            if value is None:
                logger.warning(f"类型占位符 __{var_type}__{name} 未找到对应参数，保留原值")
                return match.group(0)

            if var_type == "INT":
                return str(int(value))
            elif var_type == "FLOAT":
                return json.dumps(float(value))
            elif var_type == "BOOL":
                return json.dumps(bool(value)).lower()
            else:
                logger.warning(f"未知占位符类型 __{var_type}__，尝试通用处理")
                return json.dumps(value, ensure_ascii=False)

        workflow_str = re.sub(
            r'"__(INT|FLOAT|BOOL)__(\w+)"',
            type_replacer,
            workflow_str,
        )

        # 再处理 {{name}} 格式（字符串占位符，在引号内）
        # 模板中："text": "{{prompt}}" → 替换后："text": "actual text"
        # {{prompt}} 在引号内部，替换为原始字符串（不额外加引号）
        def string_replacer(match: re.Match) -> str:
            name = match.group(1)
            value = params.get(name)
            if value is None:
                logger.warning(f"字符串占位符 {{{{{name}}}}} 未找到对应参数，保留原值")
                return match.group(0)

            # 只返回字符串内容本身（不含引号），因为模板中已有外层引号
            # 对字符串内容做必要的转义（双引号、反斜杠等）
            if isinstance(value, str):
                # 使用 json.dumps 做转义，然后去掉外层引号
                escaped = json.dumps(value, ensure_ascii=False)[1:-1]
                return escaped
            elif isinstance(value, bool):
                return json.dumps(value).lower()
            elif isinstance(value, (int, float)):
                return json.dumps(value)
            elif value is None:
                return "null"
            else:
                escaped = json.dumps(value, ensure_ascii=False)[1:-1]
                return escaped

        workflow_str = re.sub(r"\{\{(\w+)\}\}", string_replacer, workflow_str)

        return workflow_str

    # ================================================================
    # 从工作流 dict 中提取/设置节点输入
    # ================================================================
    @staticmethod
    def _set_node_input(workflow: dict, node_id: str, field: str, value: Any) -> None:
        """安全地设置 workflow[node_id]["inputs"][field] = value"""
        if node_id not in workflow:
            return
        if "inputs" not in workflow[node_id]:
            workflow[node_id]["inputs"] = {}
        workflow[node_id]["inputs"][field] = value

    @staticmethod
    def _get_node_input(workflow: dict, node_id: str, field: str) -> Any:
        """安全地获取 workflow[node_id]["inputs"][field]"""
        node = workflow.get(node_id, {})
        inputs = node.get("inputs", {})
        return inputs.get(field)

    # ================================================================
    # 核心翻译逻辑
    # ================================================================
    def translate(self, request: GenerateRequest) -> tuple[dict[str, Any], int]:
        """
        将前端简化请求翻译为 ComfyUI 工作流 JSON。

        返回：
            (workflow_dict, resolved_seed)
            - workflow_dict: 可直接 POST 到 ComfyUI /prompt 的工作流
            - resolved_seed: 实际使用的种子

        翻译流程：
            1. 加载模板 JSON（从缓存深拷贝）
            2. 解析随机种子
            3. 构建参数字典（前端参数 + 内部计算参数）
            4. 模式特定处理（风格预设拼装 / 运动强度映射 / 上下文长度计算）
            5. 占位符替换（{{placeholder}} → 实际值）
            6. 字段映射表精确替换
            7. 返回完整工作流
        """
        # Step 1: 加载模板
        workflow = self._load_template(request.mode)

        # Step 2: 解析种子
        resolved_seed = self._resolve_seed(request.seed)

        # Step 3: 构建基础参数字典
        params = {
            "prompt": request.prompt,
            "seed": resolved_seed,
            "steps": request.steps,
            "cfg": request.cfg,
            "width": request.width,
            "height": request.height,
            "frames": request.frames,
            "input_image": request.input_image or "input.png",
            "negative_prompt": request.negative_prompt
                or DEFAULT_NEGATIVE_PROMPTS.get(
                    request.mode, DEFAULT_NEGATIVE_PROMPTS[GenerationMode.TXT2VIDEO]
                ),
        }

        # Step 4: 模式特定处理
        if request.mode == GenerationMode.VIDEO2VIDEO:
            params = self._process_video2video(request, params)
        elif request.mode in (GenerationMode.IMG2VIDEO,):
            params = self._process_img2video(request, params)
        # TXT2VIDEO 不需要额外处理，基础参数足够

        # Step 5: 占位符替换 —— 先将 workflow dict 序列化为 JSON 字符串再替换
        workflow_str = json.dumps(workflow, ensure_ascii=False, separators=(",", ":"))
        workflow_str = self._replace_placeholders(workflow_str, params)

        # 反序列化回 dict
        try:
            workflow = json.loads(workflow_str)
        except json.JSONDecodeError as e:
            raise WorkflowTemplateError(
                f"占位符替换后 JSON 解析失败：{e}。"
                f"可能原因：参数字段包含未转义的引号。原始错误附近：{str(e)}"
            )

        # Step 6: 字段映射表精确替换
        field_map = self._get_field_map(request.mode)
        for node_id, field_config in field_map.items():
            if node_id not in workflow:
                logger.warning(
                    f"字段映射表引用了不存在的工作流节点 [{node_id}]，"
                    f"请检查 {request.mode.value}.json 模板"
                )
                continue
            for field_group, field_paths in field_config.items():
                if field_group not in workflow[node_id]:
                    workflow[node_id][field_group] = {}
                for comfyui_field, param_name in field_paths.items():
                    param_value = params.get(param_name)
                    if param_value is not None:
                        workflow[node_id][field_group][comfyui_field] = param_value

        logger.info(
            f"工作流翻译完成：mode={request.mode.value}, "
            f"seed={resolved_seed}, prompt='{request.prompt[:60]}...'"
        )
        return workflow, resolved_seed

    # ================================================================
    # 视频风格化模式处理
    # ================================================================
    def _process_video2video(
        self, request: GenerateRequest, params: dict[str, Any]
    ) -> dict[str, Any]:
        """
        视频风格化模式处理：
          1. 选择风格预设，拼装完整 prompt
          2. 运动强度/直接 denoise 映射
          3. 帧数限制
        """
        style = request.style or "oil"
        if style not in STYLE_PRESETS:
            style = "oil"  # 默认油画
            logger.warning(f"未知风格预设 '{request.style}'，使用默认油画风格")

        preset = STYLE_PRESETS[style]

        # 拼装正向 prompt：{user_desc} → 用户输入
        user_desc = request.prompt if request.prompt else "a beautiful scene"
        final_prompt = preset["positive"].format(user_desc=user_desc)
        final_negative = preset["negative"]

        params["_final_prompt"] = final_prompt
        params["_final_negative"] = final_negative
        params["_controlnet_strength"] = preset["controlnet_strength"]

        # denoise：风格预设默认 vs 用户 motion_strength
        if request.denoising_strength is not None:
            params["denoising_strength"] = min(0.90, max(0.30, request.denoising_strength))
        elif request.motion_strength is not None:
            params["denoising_strength"] = motion_strength_to_denoising(
                request.motion_strength
            )
        else:
            params["denoising_strength"] = preset["denoising"]

        # cfg 也覆盖
        if request.cfg != 7.0:  # 非默认值时用户有明确意图
            params["cfg"] = request.cfg
        else:
            params["cfg"] = preset["cfg"]

        # 帧数限制（视频风格化最多处理 30 帧源视频）
        frame_cap = min(request.frames, 30)
        params["frame_cap"] = frame_cap
        params["frames"] = frame_cap

        # 传递视频路径给模板占位符
        params["video_path"] = request.video_path or "input_video.mp4"

        logger.info(
            f"V2V 风格化处理：style={style}, denoise={params['denoising_strength']}, "
            f"cfg={params['cfg']}, frame_cap={frame_cap}"
        )
        return params

    # ================================================================
    # 图生视频模式处理
    # ================================================================
    def _process_img2video(
        self, request: GenerateRequest, params: dict[str, Any]
    ) -> dict[str, Any]:
        """
        图生视频模式处理：
          1. 运动强度 → denoising_strength 映射
          2. 上下文长度计算（根据显存档位）
          3. ControlNet Tile strength
        """
        # denoise 映射
        if request.denoising_strength is not None:
            params["denoising_strength"] = min(0.90, max(0.30, request.denoising_strength))
        elif request.motion_strength is not None:
            params["denoising_strength"] = motion_strength_to_denoising(
                request.motion_strength
            )
        else:
            params["denoising_strength"] = 0.55  # 默认值

        # 上下文长度（影响 AnimateDiff 时序一致性）
        # 6GB: 16帧, 8GB+: 25帧
        context_length = request.frames
        params["context_length"] = context_length

        # ControlNet Tile strength
        params["_controlnet_strength"] = 0.6

        # 输入图路径（默认值）
        if not request.input_image:
            params["input_image"] = "input.png"

        logger.info(
            f"I2V 处理：denoise={params['denoising_strength']}, "
            f"context={context_length}, cn_strength={params['_controlnet_strength']}"
        )
        return params

    # ================================================================
    # 获取字段映射表
    # ================================================================
    def _get_field_map(self, mode: GenerationMode) -> dict[str, dict[str, dict[str, str]]]:
        """获取指定模式的字段映射表。"""
        for template_name, modes in WORKFLOW_FIELD_MAP.items():
            if mode in modes:
                return modes[mode]
        logger.warning(f"未找到模式 {mode.value} 的字段映射表，使用原始模板")
        return {}

    # ================================================================
    # 显存优化：自动降级参数
    # ================================================================
    @staticmethod
    def apply_degradation(
        workflow: dict[str, Any],
        level: int = 1,
    ) -> dict[str, Any]:
        """
        爆显存时的自动降级策略（阶梯式）。

        降级阶梯（对应甄知远报告 4.3 节）：
            Level 1: 降分辨率（width/height 各减 25%）
            Level 2: 降帧数（frames 减半，最少 8 帧）
            Level 3: 降步数（steps 减 5，最少 10 步）
            Level 4: 降 CFG（cfg 减 2.0，最低 4.0）

        对应甄知远降级链：
            OOM → 降分辨率 → 降帧数 → 降步数 → 降CFG → 提示云端
        """
        wf = copy.deepcopy(workflow)
        logger.warning(f"执行显存降级策略，当前级别：Level {level}")

        for node_id, node in wf.items():
            inputs = node.get("inputs", {})

            if level >= 1:
                if "width" in inputs and isinstance(inputs["width"], (int, float)):
                    inputs["width"] = max(256, int(inputs["width"] * 0.75))
                if "height" in inputs and isinstance(inputs["height"], (int, float)):
                    inputs["height"] = max(256, int(inputs["height"] * 0.75))

            if level >= 2:
                if "length" in inputs and isinstance(inputs["length"], (int, float)):
                    inputs["length"] = max(8, int(inputs["length"] * 0.5))
                if "frames" in inputs and isinstance(inputs["frames"], (int, float)):
                    inputs["frames"] = max(8, int(inputs["frames"] * 0.5))
                if "frame_cap" in inputs and isinstance(inputs["frame_cap"], (int, float)):
                    inputs["frame_cap"] = max(8, int(inputs["frame_cap"] * 0.5))

            if level >= 3:
                if "steps" in inputs and isinstance(inputs["steps"], (int, float)):
                    inputs["steps"] = max(10, int(inputs["steps"] - 5))

            if level >= 4:
                if "cfg" in inputs and isinstance(inputs["cfg"], (int, float)):
                    inputs["cfg"] = max(4.0, float(inputs["cfg"]) - 2.0)

        return wf

    # ================================================================
    # 清理缓存
    # ================================================================
    def clear_cache(self) -> None:
        """清理模板缓存（热重载工作流模板时使用）。"""
        self._template_cache.clear()
        logger.info("工作流模板缓存已清空")


# 全局单例
translator = WorkflowTranslator()