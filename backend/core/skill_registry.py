"""
NexusVideo Backend - 技能注册表（Skill Registry）
============================================================
Skill Registry 是 V2「Skill 内置」专项的核心基础设施。

职责：
  1. 扫描 backend/skills/<id>/manifest.json，自动发现内置技能
  2. 提供 list_skills() / get_skill() 供 API 层使用
  3. build_workflow()：根据技能 manifest + 用户参数，复用现有
     workflow_translator 的占位符机制（{{prompt}} / __INT__seed 等）
     产出可直接 POST 到 ComfyUI /prompt 的工作流 JSON
  4. check_readiness()：可选，校验技能依赖（模型/自定义节点）是否就绪

设计要点（详见 backend/docs/skill-registry-design.md）：
  - 每技能一个目录 + 可选顶层索引，新增/删除技能 = 增删目录
  - build_workflow 复用 workflow_translator._replace_placeholders，
    不重复造轮子；技能工作流直接复用现有占位符语法
  - 不依赖任何具体技能，纯骨架；首个参考技能 ref-t2v 见 backend/skills/ref-t2v/
"""

import json
import random
from pathlib import Path
from typing import Any

from loguru import logger

from config import settings
from models.schemas import (
    SkillMeta,
    SkillManifest,
    SkillMode,
)
from core.workflow_translator import WorkflowTranslator
from exceptions import SkillNotFoundError, WorkflowTemplateError


# 技能 mode(t2v|i2v|v2v) → 现有 GenerationMode 值的映射
# 仅用于把技能任务回填到 TaskRecord.mode（保持类型一致）
SKILL_MODE_TO_GEN: dict[SkillMode, str] = {
    SkillMode.T2V: "txt2video",
    SkillMode.I2V: "img2video",
    SkillMode.V2V: "video2video",
}


class SkillRegistry:
    """内置技能注册表（单例）。"""

    def __init__(self):
        self._skills: dict[str, SkillManifest] = {}
        # 工作流原始 JSON 字符串缓存：skill_id -> raw json str
        self._workflow_cache: dict[str, str] = {}
        self._loaded = False

    # ================================================================
    # 加载
    # ================================================================
    def load_all(self) -> int:
        """
        扫描 settings.skills_dir 下所有 <id>/manifest.json，加载技能。

        返回加载成功的技能数量。目录不存在时安全返回 0（不抛异常）。
        """
        self._skills.clear()
        self._workflow_cache.clear()

        skills_dir: Path = settings.skills_dir
        if not skills_dir.is_dir():
            logger.warning(
                f"技能目录不存在：{skills_dir}，跳过 Skill Registry 加载"
            )
            self._loaded = True
            return 0

        count = 0
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = self._load_manifest(manifest_path, entry)
            except Exception as e:
                logger.error(f"技能 [{entry.name}] manifest 加载失败，已跳过：{e}")
                continue
            if not manifest.enabled:
                logger.info(f"技能 [{manifest.id}] 已禁用，跳过注册")
                continue
            self._skills[manifest.id] = manifest
            count += 1
            logger.info(f"已注册技能：{manifest.id}（{manifest.name}）")

        self._loaded = True
        logger.info(f"Skill Registry 加载完成，共 {count} 个技能")
        return count

    def _load_manifest(self, manifest_path: Path, skill_dir: Path) -> SkillManifest:
        """读取并校验单个技能 manifest（含 entry 工作流文件存在性/可解析）。"""
        with open(manifest_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        entry = raw.get("entry", "workflow.json")
        wf_path = skill_dir / entry
        if not wf_path.is_file():
            raise FileNotFoundError(f"工作流文件不存在：{wf_path}")
        # 预校验工作流 JSON 可解析
        with open(wf_path, "r", encoding="utf-8") as f:
            json.load(f)
        return SkillManifest(**raw)

    def reload(self) -> int:
        """重新扫描并加载全部技能（热重载用）。"""
        return self.load_all()

    # ================================================================
    # 查询
    # ================================================================
    def list_skills(self) -> list[SkillMeta]:
        """返回全部技能摘要（供 GET /skills）。"""
        if not self._loaded:
            self.load_all()
        return [
            self._to_meta(m)
            for m in sorted(self._skills.values(), key=lambda x: x.id)
        ]

    def get_skill(self, skill_id: str) -> SkillManifest | None:
        """按 id 获取完整 manifest（供 GET /skills/{id} / build_workflow）。"""
        if not self._loaded:
            self.load_all()
        return self._skills.get(skill_id)

    @staticmethod
    def _to_meta(m: SkillManifest) -> SkillMeta:
        """manifest → 摘要（提取模型名列表，供前端 Gallery）。"""
        return SkillMeta(
            id=m.id,
            name=m.name,
            category=m.category,
            description=m.description,
            mode=m.mode,
            risk_tier=m.risk_tier,
            cloud=m.cloud,
            thumbnail=m.thumbnail,
            required_models=[d.name for d in m.required_models],
            default_params=m.default_params,
        )

    # ================================================================
    # 工作流构建（复用占位符机制）
    # ================================================================
    def build_workflow(
        self, skill_id: str, params: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], int]:
        """
        根据技能 manifest + 用户参数，构建可直接提交的 ComfyUI 工作流。

        返回：(workflow_dict, resolved_seed)

        流程：
          1. 取 manifest（不存在 → SkillNotFoundError）
          2. 合并 default_params + 用户 params（用户优先）
          3. 解析种子：params.seed 为空 → 随机；写回 params 保证链路一致
          4. 兜底负向提示词（manifest 有则用之）
          5. 加载 workflow.json（缓存深拷贝）→ 占位符替换
             （复用 workflow_translator._replace_placeholders，支持
              {{prompt}} / __INT__seed / __FLOAT__cfg 等全部语法）
          6. 可选：按 param_schema 将参数映射到节点 inputs 字段
        """
        manifest = self.get_skill(skill_id)
        if manifest is None:
            raise SkillNotFoundError(skill_id)

        merged: dict[str, Any] = {**manifest.default_params, **(params or {})}

        # 解析种子（与 workflow_translator._resolve_seed 同策略）
        seed = merged.get("seed")
        if seed is None:
            seed = random.randint(0, 2**32 - 1)
        merged["seed"] = seed

        # 兜底负向提示词
        if "negative_prompt" not in merged and manifest.default_params.get(
            "negative_prompt"
        ):
            merged["negative_prompt"] = manifest.default_params["negative_prompt"]

        # 加载工作流（带缓存）
        workflow = self._load_workflow(skill_id, manifest.entry)

        # 占位符替换（复用现有机制，零重复实现）
        workflow_str = json.dumps(workflow, ensure_ascii=False, separators=(",", ":"))
        workflow_str = WorkflowTranslator._replace_placeholders(workflow_str, merged)
        try:
            workflow = json.loads(workflow_str)
        except json.JSONDecodeError as e:
            raise WorkflowTemplateError(
                f"技能 [{skill_id}] 工作流占位符替换后 JSON 解析失败：{e}。"
                f"可能原因：参数字段包含未转义的引号。"
            )

        # 可选：param_schema 字段映射（节点 inputs 精确写入）
        # 仅当 manifest 声明了 param_schema 且参数存在时生效；
        # 依赖 {{placeholder}} 的技能无需声明，此处为空操作。
        param_schema = manifest.param_schema or {}
        for param_name, mapping in param_schema.items():
            if param_name not in merged:
                continue
            node_id = str(mapping.get("node"))
            field = mapping.get("field")
            if not node_id or not field:
                continue
            if node_id not in workflow:
                continue
            if "inputs" not in workflow[node_id]:
                workflow[node_id]["inputs"] = {}
            workflow[node_id]["inputs"][field] = merged[param_name]

        return workflow, seed

    def _load_workflow(self, skill_id: str, entry: str) -> dict[str, Any]:
        """加载技能工作流 JSON（缓存原始字符串，每次返回深拷贝）。"""
        if skill_id not in self._workflow_cache:
            wf_path = settings.skills_dir / skill_id / entry
            if not wf_path.is_file():
                raise WorkflowTemplateError(
                    f"技能 [{skill_id}] 工作流文件不存在：{wf_path}"
                )
            try:
                with open(wf_path, "r", encoding="utf-8") as f:
                    raw = f.read()
                json.loads(raw)  # 预校验
                self._workflow_cache[skill_id] = raw
            except json.JSONDecodeError as e:
                raise WorkflowTemplateError(
                    f"技能 [{skill_id}] 工作流 JSON 解析失败：{e}"
                )
        return json.loads(self._workflow_cache[skill_id])

    # ================================================================
    # 依赖就绪预检（可选）
    # ================================================================
    def check_readiness(self, skill_id: str) -> dict[str, Any]:
        """
        校验技能依赖是否就绪（供 GET /skills/{id}/readiness）。

        P1 实现：校验 manifest + 工作流文件完整性。
        模型/自定义节点文件的实际探测需 ComfyUI 模型根目录配置，
        留待后续接入；此处声明依赖供前端展示 missing 列表，
        真实缺失由 ComfyUI 运行时在生成阶段反馈（ComfyUINodeError 等）。
        """
        manifest = self.get_skill(skill_id)
        if manifest is None:
            raise SkillNotFoundError(skill_id)

        missing_models: list[str] = []
        missing_nodes: list[str] = []

        # 工作流文件完整性（entry 存在性）
        wf_path = settings.skills_dir / skill_id / manifest.entry
        if not wf_path.is_file():
            missing_models.append(f"(workflow) {manifest.entry}")

        return {
            "ready": len(missing_models) == 0 and len(missing_nodes) == 0,
            "missing_models": missing_models,
            "missing_nodes": missing_nodes,
            "note": (
                "模型/自定义节点文件探测需 ComfyUI 模型目录支持，"
                "当前仅校验工作流完整性；生成时由 ComfyUI 运行时反馈真实缺失。"
            ),
        }


# 全局单例
skill_registry = SkillRegistry()
