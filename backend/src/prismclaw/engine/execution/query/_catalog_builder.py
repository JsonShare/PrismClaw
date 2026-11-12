from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prismclaw.engine.execution.capabilities.catalog import (
    CapabilityCatalogService,
)
from prismclaw.engine.execution.request.system_prompt import (
    AvailableSkillEntry,
    AvailableSubagentEntry,
    MarketplaceSummary,
    _subagent_compact_description,
    _truncate_by_tokens,
)
from prismclaw.engine.subagent.runtime.follow_up import (
    build_session_follow_up_note,
    session_follow_up_snapshot,
)
from prismclaw.schema.agent.types import WorkspaceMode
from prismclaw.schema.services.skills import SkillInvocationState
from prismclaw.schema.shared.utils import estimate_tokens

logger = logging.getLogger(__name__)

_REPO_MAP_IGNORE_DIRS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    "__pycache__",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "coverage",
    ".tox",
    ".pytest_cache",
    "uploads",
    "outputs",
    "tmp",
    ".envs",
    ".work",
    "skills",
    "sandbox",
}


def _session_follow_up_notes(tree: Any, tool_summaries: list[tuple[str, str]]) -> list[str]:
    return build_session_follow_up_note(
        session_follow_up_snapshot(tree),
        available_tools={name for name, _desc in tool_summaries},
    )


def _build_lightweight_repo_map(
    workspace_path: str | None,
    max_depth: int = 2,
    max_files: int = 50,
) -> str | None:
    if not workspace_path or not os.path.isdir(workspace_path):
        return None

    root = Path(workspace_path)
    lines = []
    file_count = 0
    truncated = False

    def walk(dir_path: Path, current_depth: int, prefix: str = "") -> None:
        nonlocal file_count, truncated

        if truncated:
            return

        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except (PermissionError, FileNotFoundError, OSError):
            return

        valid_entries = []
        for entry in entries:
            if entry.name in _REPO_MAP_IGNORE_DIRS:
                continue
            if entry.name.startswith(".") and entry.name not in (
                ".github",
                ".env.example",
                ".gitignore",
                ".dockerignore",
            ):
                continue
            valid_entries.append(entry)

        for index, entry in enumerate(valid_entries):
            if file_count >= max_files:
                truncated = True
                return

            is_last = index == len(valid_entries) - 1
            connector = "└── " if is_last else "├── "
            display_name = f"{entry.name}/" if entry.is_dir() else entry.name
            lines.append(f"{prefix}{connector}{display_name}")
            file_count += 1

            if entry.is_dir() and current_depth < max_depth:
                extension = "    " if is_last else "│   "
                walk(Path(entry.path), current_depth + 1, prefix + extension)

    lines.append(root.name or ".")
    walk(root, 1)

    if truncated:
        lines.append("……（已截断，可用 'ls' 进一步探索）")

    if len(lines) <= 1:
        return None

    return "\n".join(lines)


def _tool_prompt_summaries(
    *,
    capability_catalog: CapabilityCatalogService,
    skill_invocation: SkillInvocationState | None,
    discovered_tools: set[str] | None,
    mode: WorkspaceMode,
    subagent_depth: int,
    headless: bool = False,
) -> list[tuple[str, str]]:
    return [
        (entry.llm_name, entry.description)
        for entry in capability_catalog.list_visible_tool_entries(
            skill_invocation=skill_invocation,
            discovered_tools=discovered_tools,
            mode=mode,
            subagent_depth=subagent_depth,
            headless=headless,
        )
    ]


def _subagent_catalog_state(catalog: CapabilityCatalogService | None) -> tuple[int, bool]:
    if catalog is None:
        return 0, False
    entries = catalog.list_agent_entries()
    return len(entries), any(entry.can_handoff_to for entry in entries)


def _subcaps():
    """惰性读取子额度配置；加载失败时回退到内置默认值（等价于当前行为）。"""
    try:
        from prismclaw.infra.settings import load_settings
        return load_settings().agent.context.budget.subcaps
    except Exception:
        return None


def _compact_description(text: str | None, max_len: int | None = None) -> str:
    if not text:
        return ""
    if max_len is None:
        caps = _subcaps()
        max_len = caps.listing_desc_chars if caps is not None else 250
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[:max_len].rstrip() + "…"


def _rough_token_count(text: str) -> int:
    """Rough token estimation: ~4 chars per token for English, ~1.5 for CJK."""
    if not text:
        return 0
    cjk_count = sum(1 for c in text if ord(c) > 0x4e00)
    ascii_count = len(text) - cjk_count
    return max(1, int(ascii_count / 4 + cjk_count / 1.5))


def _skill_catalog_budget_tokens(context_window_tokens: int | None) -> int:
    """Token budget for the skill catalog (~1% of context window).

    修复 F7: 统一使用 token 估算函数，不再混用 character count。
    比例来自 subcaps.skills_ratio，加载失败时回退 0.01。
    """
    caps = _subcaps()
    ratio = caps.skills_ratio if caps is not None else 0.01
    if not context_window_tokens or context_window_tokens <= 0:
        return 800
    return max(200, int(context_window_tokens * ratio))


def _entry_render_cost(entry: AvailableSkillEntry) -> int:
    """计算单条技能在 skills_section() 契约式渲染下的 token 开销。

    计费口径 = name + compact description（构建期已经
    _subagent_compact_description 截断）。技能行的标点装饰
    （"- " / 反引号 / " — "）与 marketplace 组注释行开销 ≤ 数 token，
    忽略不计，换取计费函数纯净。
    """
    return _rough_token_count(entry.name) + _rough_token_count(entry.description)


def _hard_cap_names(
    entries: "list[AvailableSkillEntry]",
    budget_tokens: int,
) -> "tuple[list[AvailableSkillEntry], str]":
    """Truncate the listed skills so they fit the budget (hard cap).

    逐条计费：每条按 _entry_render_cost（name + compact desc）累计，
    与 skills_section() 契约式渲染的逐技能行格式对齐。active 优先保留、
    其余按 (-priority, name) 排序的既有语义不变。
    """
    active = next((e for e in entries if e.is_active), None)
    rest = sorted(
        (e for e in entries if e is not active),
        key=lambda e: (-e.priority, e.name),
    )
    kept: list[AvailableSkillEntry] = []
    running = 0

    if active is not None:
        kept.append(active)
        running += _entry_render_cost(active)
    for entry in rest:
        cost = _entry_render_cost(entry)
        if kept and running + cost > budget_tokens:
            break
        kept.append(entry)
        running += cost
    omitted = len(entries) - len(kept)
    if omitted <= 0:
        return kept, ""
    kept.sort(key=lambda e: e.name)
    note = f"另有 {omitted} 个插件技能未在此列出，使用 `skill_search` 按主题检索。"
    return kept, note


def _within_budget_catalog(
    entries: "list[AvailableSkillEntry]",
    budget_tokens: int,
) -> "tuple[tuple[AvailableSkillEntry, ...], tuple[MarketplaceSummary, ...], str]":
    """§4.10 静态层：项目技能全量保留，marketplace 技能行受预算硬截断，
    marketplace 包索引（summaries）从截断前全量聚合并永不截断。

    - 项目技能（marketplace_name 为空）是用户主动安装的能力，数量有界，
      不参与预算——其可见性是静态层的基线职责。
    - marketplace 技能行超预算时按 ``_hard_cap_names`` 逐条截断，被裁
      技能经 ``skill_search`` 按需可达。
    - summaries（包索引行）与技能行解耦：即使组内技能行全部被裁，包索引
      仍渲染，模型始终知道"存在某个能力域"。frozen prefix 尺寸因此与
      技能总数解耦（组行数 = marketplace 数）。

    Returns ``(entries, summaries, note)``；note 仅在技能行被截断时非空。
    """
    project_entries = [e for e in entries if not e.marketplace_name]
    plugin_entries = [e for e in entries if e.marketplace_name]

    # 包索引从截断前的全量插件技能聚合（含被裁技能计数）
    summaries: list[MarketplaceSummary] = []
    by_marketplace: dict[str, list[AvailableSkillEntry]] = {}
    for e in plugin_entries:
        by_marketplace.setdefault(e.marketplace_name or "", []).append(e)
    for mp_name in sorted(by_marketplace):
        group = by_marketplace[mp_name]
        desc = next((e.marketplace_description for e in group if e.marketplace_description), "")
        summaries.append(
            MarketplaceSummary(name=mp_name, description=desc or "", skill_count=len(group))
        )

    if budget_tokens <= 0:
        kept_plugin, note = _hard_cap_names(plugin_entries, 1)
    elif sum(_entry_render_cost(e) for e in plugin_entries) <= budget_tokens:
        kept_plugin, note = plugin_entries, ""
    else:
        kept_plugin, note = _hard_cap_names(plugin_entries, budget_tokens)

    kept = sorted([*project_entries, *kept_plugin], key=lambda e: e.name)
    return tuple(kept), tuple(summaries), note


def _effective_context_window_tokens(model: str, deps: Any) -> int | None:
    """Effective context window (tokens) for the model, fully config-driven.

    Values come from ``deps.config`` (loaded from backend/configs/server.yaml):
    the summary output cap from ``compaction.history.maxOutputTokensForSummary``
    and the unknown-model fallback from ``agent.context.budget.window_tokens``.
    No window literals live in code.
    """
    config = getattr(deps, "config", None)
    if config is None:
        return None
    try:
        from prismclaw.engine.execution.request.model_fallback import get_effective_context_window
    except Exception:
        return None
    try:
        return get_effective_context_window(
            model,
            max_output_tokens_for_summary=config.context.compaction.history.summary_output_token_cap,
            fallback=config.context.budget.window_tokens,
        )
    except Exception:
        return None


def _build_available_skills_catalog(
    *,
    capability_catalog: CapabilityCatalogService | None,
    workspace_path: str | None,
    tool_summaries: list[tuple[str, str]],
    skill_invocation: SkillInvocationState | None,
    context_window_tokens: int | None = None,
) -> "tuple[tuple[AvailableSkillEntry, ...], tuple[MarketplaceSummary, ...], str]":
    """Deterministic skill catalog exposed to the model (semi-pull).

    Returns ``(entries, marketplace_summaries, note)``：

    - entries：按字母序的可见技能行快照。项目技能全量保留（用户主动安装，
      数量有界）；marketplace 技能行超预算时硬截断，被裁技能经
      ``skill_search`` 按需可达（§4.10 静态层）。
    - marketplace_summaries：包索引行，从截断前全量聚合、永不截断——
      模型始终知道"存在某个能力域"，frozen prefix 尺寸与技能总数解耦。
    - note：仅在技能行被截断时非空。

    This catalog is the authoritative skill surface: the model reads each
    skill's name and description; matched skills are auto-activated.
    There is deliberately NO rule-based or keyword matcher
    here — matching is model-driven (the dynamic skill catalog is the single
    source of truth, so nothing can drift out of sync with installed skills).

    Full exposure + active marker. Skills are ALWAYS visible (no exclusive
    hiding). The currently-active skill is marked with is_active=True so the
    model can recognize lifecycle state without losing cross-skill
    discoverability.

    Budget. The listing targets ~1% of the context window. The budget is a
    HARD cap on marketplace skill rows only; project skills and marketplace
    summaries are budget-exempt (§4.10).
    """
    if capability_catalog is None or not workspace_path:
        return (), (), ""
    tool_names = {name for name, _desc in tool_summaries}
    if "skill_search" not in tool_names:
        return (), (), ""
    try:
        catalog_entries = capability_catalog.list_skill_entries(str(workspace_path))
    except Exception:
        logger.warning("available_skills_catalog.list_skills_failed", exc_info=True)
        return (), (), ""
    if not catalog_entries:
        return (), (), ""
    active_skill_name: str | None = (
        skill_invocation.skill_name if skill_invocation is not None else None
    )
    ordered = sorted(catalog_entries, key=lambda e: e.name)
    budget = _skill_catalog_budget_tokens(context_window_tokens)
    built: list[AvailableSkillEntry] = []
    for entry in ordered:
        manifest = entry.manifest
        # invocation_policy 过滤：
        # - AUTO / MODEL_ONLY → 模型可见（出现在 available_skills 列表）
        # - USER_ONLY → 仅用户指定可见，不出现在模型列表
        # - DISABLED → 完全禁用，不出现在任何列表
        policy = getattr(manifest, "invocation_policy", None)
        policy_str = str(policy.value) if policy is not None else "auto"
        if policy_str in ("user_only", "disabled"):
            continue
        built.append(
            AvailableSkillEntry(
                name=entry.name,
                description=_subagent_compact_description(manifest.description),
                priority=manifest.priority,
                is_active=(active_skill_name is not None and active_skill_name == entry.name),
                marketplace_name=entry.marketplace_name,
                marketplace_description=entry.marketplace_description,
                invocation_policy=policy_str,
            )
        )
    return _within_budget_catalog(built, budget)


def _build_available_subagents_catalog(
    *,
    capability_catalog: CapabilityCatalogService | None,
    tool_summaries: list[tuple[str, str]],
    token_budget: int | None = None,
) -> tuple[tuple[AvailableSubagentEntry, ...], str, int]:
    """Single-source subagent catalog for the frozen prompt.

    Single catalog source: CapabilityCatalogService.list_agent_entries(). This
    is exactly the same registry+graph used at dispatch time, so prompt-listed
    names and runtime-accepted names CANNOT drift apart. refreshed_at is
    stamped in ISO-8601 UTC so the model can reconcile stale cached blocks vs
    current runtime state.

    SDD D11：subagent 清单是冻结区最大的增长源（现场 3048 tokens 占 73%）。
    现按 token 预算截断（缺省为现场基线的 ~50%），返回 (条目, refreshed_at,
    总数) 让渲染侧附加「…及 N 个」trailer——渐进披露，模型可用 sub_agent
    工具按需发现。
    """
    if capability_catalog is None:
        return (), "", 0
    tool_names = {name for name, _desc in tool_summaries}
    if "sub_agent" not in tool_names:
        return (), "", 0
    try:
        catalog_entries = capability_catalog.list_agent_entries()
    except Exception:
        logger.warning("available_subagents_catalog.list_agents_failed", exc_info=True)
        return (), "", 0
    if not catalog_entries:
        return (), "", 0
    if token_budget is None:
        caps = _subcaps()
        token_budget = caps.subagents if caps is not None else 1500
    ordered = sorted(catalog_entries, key=lambda e: e.name)
    total_count = len(ordered)
    built: list[AvailableSubagentEntry] = []
    used_tokens = 0
    for entry in ordered:
        manifest = entry.manifest
        candidate = AvailableSubagentEntry(
            name=entry.name,
            description=_subagent_compact_description(manifest.description),
            can_handoff_to=tuple(entry.can_handoff_to or ()),
            source=entry.plugin_name,
            # v5 渐进式披露：agent marketplace 元数据
            marketplace_name=entry.marketplace_name,
            marketplace_description=entry.marketplace_description,
        )
        # 渲染格式（见 system_prompt.subagents_section）：
        #   名称索引里出现一次 `name`，详细列表里又出现一次 `name`，
        #   外加 description、可选的 marketplace 方括号、可选 can_handoff_to 行。
        #   旧成本函数只算了 name+desc+mp_desc 各一次，实测漏算 ~50%。
        mp_desc_30 = (
            _truncate_by_tokens(str(candidate.marketplace_description), 30)
            if candidate.marketplace_description else ""
        )
        mp_bracket = (
            f" [{candidate.marketplace_name}{' — ' + mp_desc_30 if mp_desc_30 else ''}]"
            if candidate.marketplace_name else ""
        )
        handoff_line = (
            f"  → can_handoff_to: {', '.join(f'`{n}`' for n in candidate.can_handoff_to)}"
            if candidate.can_handoff_to else ""
        )
        # name 出现两次（索引 + 详情行前缀）
        cost = estimate_tokens(
            candidate.name * 2
            + str(candidate.description)
            + mp_bracket
            + handoff_line
        )
        if built and used_tokens + cost > token_budget:
            break
        built.append(candidate)
        used_tokens += cost
    refreshed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return tuple(built), refreshed_at, total_count
