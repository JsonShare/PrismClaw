from __future__ import annotations

from typing import Any, TYPE_CHECKING

from prismclaw.engine.compression.retention import (
    SectionRetention,
    apply_section_retention,
)
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.services.skills import SkillInvocationState
from prismclaw.schema.shared.utils import estimate_tokens

if TYPE_CHECKING:
    # 类型注解所需，runtime 通过 ports 访问。
    from prismclaw.engine.execution.checkpoint import RunSnapshot
    from prismclaw.engine.execution.checkpoint.models import InvokedSkillState


def project_preservation_runtime(
    *,
    request: Any,
    snapshot: RunSnapshot,
    tool_ctx: ToolContext,
) -> None:
    system_prompt_sections_payload = snapshot.system_prompt_sections.model_dump(mode="json")
    invoked_skill_state_payload = snapshot.invoked_skill_state.model_dump(mode="json")
    tool_ctx.system_prompt_sections = system_prompt_sections_payload
    tool_ctx.invoked_skill_state = invoked_skill_state_payload


def _apply_active_sections_from_preflight(
    *,
    request: Any,
    snapshot: RunSnapshot,
    tool_ctx: ToolContext,
    system_prompt_sections: Any,
    retention: SectionRetention,
) -> None:
    snapshot.system_prompt_sections = apply_section_retention(
        system_prompt_sections,
        retention,
    )
    project_preservation_runtime(
        request=request,
        snapshot=snapshot,
        tool_ctx=tool_ctx,
    )


def apply_section_retention_outcome(
    *,
    request: Any,
    snapshot: RunSnapshot,
    tool_ctx: ToolContext | None,
    outcome: Any,
) -> None:
    retention = getattr(outcome, "section_retention", SectionRetention.unchanged())
    if not isinstance(retention, SectionRetention):
        raise ValueError(f"invalid section retention contract: {retention!r}")
    if retention.source == "preflight":
        if tool_ctx is None:
            raise ValueError("preflight section retention requires tool context")
        preflight = getattr(outcome, "preflight", None)
        if preflight is None:
            raise ValueError("preflight section retention requires preflight system prompt sections")
        _apply_active_sections_from_preflight(
            request=request,
            snapshot=snapshot,
            tool_ctx=tool_ctx,
            system_prompt_sections=preflight.system_prompt_sections,
            retention=retention,
        )
        return
    if retention.source == "snapshot":
        _apply_durable_static_sections(
            request=request,
            snapshot=snapshot,
            tool_ctx=tool_ctx,
            retention=retention,
        )
        return
    if retention.source != "unchanged":
        raise ValueError(f"unknown section retention source: {retention.source!r}")


def project_active_skill_invocation(
    *,
    request: Any,
    snapshot: RunSnapshot,
    tool_ctx: ToolContext,
    skill_invocation: SkillInvocationState | None,
) -> None:
    snapshot.invoked_skill_state.active_invocation = skill_invocation
    project_preservation_runtime(
        request=request,
        snapshot=snapshot,
        tool_ctx=tool_ctx,
    )


def _apply_durable_static_sections(
    *,
    request: Any,
    snapshot: RunSnapshot,
    tool_ctx: ToolContext | None,
    retention: SectionRetention,
) -> None:
    snapshot.system_prompt_sections = apply_section_retention(
        snapshot.system_prompt_sections,
        retention,
    )
    if tool_ctx is None:
        return
    project_preservation_runtime(
        request=request,
        snapshot=snapshot,
        tool_ctx=tool_ctx,
    )


async def cleanup_preservation_runtime_on_terminal_exit(
    *,
    session_store: Any,
    run_snapshot_store: Any,
    request: Any,
    tree: Any,
    snapshot: RunSnapshot | None,
    tool_ctx: ToolContext | None,
    ports: Any,
) -> SkillInvocationState | None:
    if snapshot is None or tool_ctx is None:
        return None

    cleaned_sections = apply_section_retention(
        snapshot.system_prompt_sections,
        SectionRetention.durable_static(),
    )
    cleaned_skills = snapshot.invoked_skill_state.retain_durable_records()
    changed = (
        cleaned_sections != snapshot.system_prompt_sections
        or cleaned_skills != snapshot.invoked_skill_state
    )
    snapshot.system_prompt_sections = cleaned_sections
    snapshot.invoked_skill_state = cleaned_skills
    cleaned_invocation = snapshot.invoked_skill_state.active_invocation

    project_active_skill_invocation(
        request=request,
        snapshot=snapshot,
        tool_ctx=tool_ctx,
        skill_invocation=cleaned_invocation,
    )

    if not changed or session_store is None or tree is None:
        return cleaned_invocation

    await ports.persist_run_snapshot(
        session_store,
        tree,
        snapshot,
        snapshot_store=run_snapshot_store,
    )
    return cleaned_invocation


def build_invoked_skill_attachment(
    invoked_skill_state: InvokedSkillState,
) -> str | None:
    """从 invoked_skill_state 构建技能附件文本，用于压缩后重新注入。

    对标参考实现 createSkillAttachmentIfNeeded:
    将所有已调用技能的 rendered_prompt_fragment 拼接为一个 attachment。
    仅包含 clear_after_terminal_result=False 的持久记录。
    总量受 subcaps.skill_attachment 约束（CJK 感知 token 估算）。
    """
    try:
        from prismclaw.infra.settings import load_settings
        max_tokens = load_settings().agent.context.budget.subcaps.skill_attachment
    except Exception:
        max_tokens = 6000

    if not invoked_skill_state.skills:
        return None

    fragments: list[str] = []
    total_tokens = 0
    for name, record in invoked_skill_state.skills.items():
        if not record.rendered_prompt_fragment:
            continue
        fragment = f"[Invoked Skill: {name}]\n{record.rendered_prompt_fragment}"
        fragment_tokens = estimate_tokens(fragment)
        if total_tokens + fragment_tokens > max_tokens:
            break
        fragments.append(fragment)
        total_tokens += fragment_tokens + 2  # "\n\n" separator

    if not fragments:
        return None
    return "\n\n".join(fragments)
