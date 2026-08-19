#!/usr/bin/env python3
"""
工作流模板验证工具。校验 workflows/ 目录下 JSON 工作流的完整性。

用法:
    python backend/scripts/validate_workflow.py
"""
import json
import sys
import re
from pathlib import Path

# ── 已知的 ComfyUI 原生节点 ──
KNOWN_NATIVE = {
    "CLIPTextEncode",
    "EmptyHunYuanLatentVideo",
    "KSampler",
    "UNETLoader",
    "VAELoader",
    "CLIPLoader",
    "VAEDecodeTiled",
    "VAEDecode",
    "FFMPEG_VideoCombine",
    "SaveImage",
    "LoadImage",
    "ConditioningSetT5Attn",
    "ModelSamplingSD3",
}

# ── 需要自定义节点（需确认 ComfyUI 已安装对应插件）──
KNOWN_CUSTOM = {
    # 文生视频核心
    "WanVideoWrapper",
    # 插帧
    "RIFEFrameInterpolator",
    # CLIP 控制
    "CLIPSetLastLayer",
    # 图生视频 / ControlNet
    "VAEEncode",
    "ControlNetApplyAdvanced",
    "ControlNetLoader",
    "CheckpointLoaderSimple",
    # AnimateDiff 系列
    "AnimateDiffModelLoader",
    "AnimateDiffSampler",
    "ADE_basicContextOptions",
    "ADE_AnimateDiffCombine",
    "ADE_EmptyFrameLatentVideo",
    "ADE_standardAnimateDiffModel",
    # 视频处理
    "VHS_LoadVideo",
    "Preprocessor",
}

# ── 占位符正则 ──
PLACEHOLDER_PATTERN = re.compile(r"\{\{[^}]+\}\}")
TYPE_PLACEHOLDER_PATTERN = re.compile(r"__[A-Z]+__")


def validate_workflow(path: Path) -> dict:
    """对单个工作流 JSON 执行完整性校验。"""
    result: dict = {"path": str(path), "valid": True, "errors": [], "warnings": []}

    # ── 1. JSON 解析 ──
    try:
        with open(path, "r", encoding="utf-8") as f:
            workflow = json.load(f)
    except Exception as e:
        result["valid"] = False
        result["errors"].append(f"JSON 解析失败: {e}")
        return result

    # 顶层必须是 dict
    if not isinstance(workflow, dict):
        result["valid"] = False
        result["errors"].append("顶层 JSON 不是 object")
        return result

    # ── 2. 节点 ID 唯一性 ──
    ids = list(workflow.keys())
    if len(ids) != len(set(ids)):
        result["valid"] = False
        result["errors"].append("存在重复节点 ID")

    # ── 3. 节点连接完整性 + class_type 白名单 ──
    for node_id, node in workflow.items():
        class_type = node.get("class_type", "")

        # class_type 检查
        if class_type in KNOWN_NATIVE:
            pass
        elif class_type in KNOWN_CUSTOM:
            result["warnings"].append(
                f"节点 [{node_id}] 使用自定义节点 {class_type}，需确认 ComfyUI 已安装对应插件"
            )
        else:
            result["warnings"].append(
                f"节点 [{node_id}] 使用未知 class_type: {class_type}"
            )

        # 输入连接引用检查
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            result["errors"].append(f"节点 [{node_id}] 的 inputs 不是 dict")
            continue

        for field, value in inputs.items():
            if isinstance(value, list) and len(value) >= 2 and isinstance(value[0], str):
                ref_id = value[0]
                if ref_id not in workflow:
                    result["valid"] = False
                    result["errors"].append(
                        f"节点 [{node_id}] 字段 '{field}' 引用了不存在的节点 [{ref_id}]"
                    )

    # ── 4. 占位符统计 ──
    raw = json.dumps(workflow)
    placeholders = PLACEHOLDER_PATTERN.findall(raw)
    type_placeholders = TYPE_PLACEHOLDER_PATTERN.findall(raw)

    if placeholders:
        uniq = sorted(set(placeholders))
        result["warnings"].append(
            f"存在 {len(placeholders)} 个字符串占位符（由 workflow_translator 替换）: {uniq}"
        )
    if type_placeholders:
        uniq_t = sorted(set(type_placeholders))
        result["warnings"].append(
            f"存在 {len(type_placeholders)} 个类型占位符（由 workflow_translator 替换）: {uniq_t}"
        )

    result["node_count"] = len(ids)
    result["placeholder_count"] = len(placeholders)
    result["type_placeholder_count"] = len(type_placeholders)
    return result


def main() -> int:
    root = Path(__file__).resolve().parents[2]

    # 优先检查项目根目录的 workflows/，再检查 backend/workflows/
    candidates = [
        root / "workflows",
        root / "backend" / "workflows",
    ]

    wf_dir: Path | None = None
    for c in candidates:
        if c.is_dir():
            wf_dir = c
            break

    if wf_dir is None:
        print("[FAIL] 未找到 workflows/ 目录")
        return 1

    names = ["txt2video.json", "img2video.json", "video2video.json"]
    all_valid = True

    for name in names:
        path = wf_dir / name
        if not path.exists():
            print(f"[SKIP] {name} 不存在")
            continue

        print(f"\n{'=' * 60}")
        print(f"验证: {name}")
        print(f"{'=' * 60}")

        result = validate_workflow(path)
        print(f"  路径: {result['path']}")
        print(f"  节点数: {result.get('node_count', '?')}")
        print(f"  字符串占位符: {result.get('placeholder_count', 0)}")
        print(f"  类型占位符: {result.get('type_placeholder_count', 0)}")
        print(f"  状态: {'✅ 通过' if result['valid'] else '❌ 失败'}")

        if result["errors"]:
            all_valid = False
            for e in result["errors"]:
                print(f"    ❌ {e}")
        if result["warnings"]:
            for w in result["warnings"]:
                print(f"    ⚠️  {w}")

    if all_valid:
        print(f"\n{'=' * 60}")
        print("总结: ✅ 所有工作流验证通过")
        print(f"{'=' * 60}")
    else:
        print(f"\n{'=' * 60}")
        print("总结: ❌ 存在验证失败的工作流，请检查上方错误")
        print(f"{'=' * 60}")

    return 0 if all_valid else 1


if __name__ == "__main__":
    sys.exit(main())