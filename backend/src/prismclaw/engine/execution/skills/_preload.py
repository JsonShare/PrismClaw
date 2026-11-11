"""Skill preload message builder — moved from skills._preload_utils.

This module belongs to the engine layer because its only consumer is
SkillRuntime (engine/agent/skills/runtime.py). It was previously in
the skills package, creating an unnecessary engine→skills dependency.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from prismclaw.engine.execution.skills.doc_inline import _inline_referenced_docs
from prismclaw.schema.shared.utils import estimate_tokens

logger = logging.getLogger(__name__)


def _coerce_skill_manifest_preload(skill: Any):
    """Lightweight skill manifest normalization."""
    from prismclaw.schema.services.skills import SkillManifest
    if isinstance(skill, SkillManifest):
        return skill
    return SkillManifest.model_validate(skill)


def _build_preload_messages(
    runtime: Any,
    preload_skills: list[str],
    *,
    workspace_path: Path,
    token_limit: int = 7500,
) -> str:
    """Build preload messages for sub-skills referenced by a workflow.

    Progressively discloses skill content based on available token budget:
      1. Full preload — entire skill content within budget.
      2. Partial preload — head snippet + reference when tight on budget.
      3. Name listing — just name + description when no budget remains.

    runtime must provide resolve_skill(name, workspace_path=...) method.

    Uses token-based budgeting (CJK-aware) instead of character-based
    to prevent CJK content from consuming disproportionately more context.
    """
    if not preload_skills:
        return ""
    blocks: list[str] = []
    used_tokens = 0
    skipped: list[tuple[str, str]] = []

    for name in preload_skills:
        if used_tokens >= token_limit:
            remaining_names = preload_skills[preload_skills.index(name):]
            for rn in remaining_names:
                skipped.append((rn, ""))
            break
        try:
            sub = runtime.resolve_skill(name, workspace_path=workspace_path)
        except Exception:
            continue
        if sub is None:
            continue
        try:
            sub_m = _coerce_skill_manifest_preload(sub)
        except Exception:
            continue
        try:
            sub_body = _inline_referenced_docs(sub_m.body or "", sub_m.file_path or "")
        except Exception:
            sub_body = sub_m.body or ""
        if not sub_body.strip():
            continue

        description = (sub_m.description or "")[:160]
        block = f"\n---\n## 已预加载子技能：{name}\n{sub_body.strip()}\n"
        block_tokens = estimate_tokens(block)
        remaining = token_limit - used_tokens

        if block_tokens <= remaining:
            blocks.append(block)
            used_tokens += block_tokens
        elif remaining >= 200:
            # 粗略估算截断长度：token_limit → 字符 ≈ tokens * 2 (保守)
            char_budget = remaining * 2
            snippet = sub_body.strip()[:char_budget]
            partial_block = (
                f"\n---\n## 部分预加载（预算受限）：{name}\n"
                f"{snippet}\n"
                f"…… [完整技能内容共 {len(sub_body)} 字符，未展示。"
                f"该技能在被引用时会自动激活。]\n"
            )
            blocks.append(partial_block)
            used_tokens += estimate_tokens(partial_block)
            if description:
                skipped.append((name, f"（部分预加载）{description}"))
        else:
            if description:
                skipped.append((name, description))
            else:
                skipped.append((name, ""))

    if not blocks and not skipped:
        return ""

    header = (
        "以下是由当前工作流引用的子技能预加载内容"
        "（不要通过文件系统读取这些文件——内容已包含在上下文中）。\n"
    )
    result = header + "".join(blocks)

    if skipped:
        result += "\n## 未完整预加载的子技能：\n"
        for name, desc in skipped:
            line = f"- `{name}`"
            if desc:
                line += f": {desc}"
            line += "\n"
            result += line

    return result
