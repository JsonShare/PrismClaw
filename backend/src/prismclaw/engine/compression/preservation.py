from __future__ import annotations

from typing import TYPE_CHECKING

from prismclaw.engine.compression.retention import (
    SectionRetention,
    apply_section_retention,
)
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.services.skills import SkillInvocationState

if TYPE_CHECKING:
    # 类型注解所需，runtime 通过 ports 访问。
    from prismclaw.engine.compression.ports import ContextRuntimePorts
    from prismclaw.engine.execution.checkpoint import RunSnapshot
    from prismclaw.engine.execution.checkpoint.models import SystemPromptSectionState
    from prismclaw.schema.agent.types import RunRequest
    from prismclaw.schema.session import SessionTree
    from prismclaw.schema.storage.persistence_types import RunSnapshotStore
    from prismclaw.schema.storage.storage_types import SessionStore


def project_preservation_runtime(
    *,
    request: RunRequest,
    snapshot: RunSnapshot,
    tool_ctx: ToolContext,
) -> None:
    system_prompt_sections_payload = snapshot.system_prompt_sections.model_dump(mode="json")
    invoked_skill_state_payload = snapshot.invoked_skill_state.model_dump(mode="json")
    tool_ctx.system_prompt_sections = system_prompt_sections_payload
    tool_ctx.invoked_skill_state = invoked_skill_state_payload


def _apply_active_sections_from_preflight(
    *,
    request: RunRequest,
    snapshot: RunSnapshot,
    tool_ctx: ToolContext,
    system_prompt_sections: SystemPromptSectionState,
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
    request: RunRequest,
    snapshot: RunSnapshot,
    tool_ctx: ToolContext | None,
    outcome: object,
) -> None:
    retention = getattr(outcome, "section_retention", SectionRetention.unchanged())
    if not isinstance(retention, SectionRetention):
        raise ValueError(f"invalid section retention contract: {retention!r}")
    if retention.source == "preflight":
        if tool_ctx is None:
            raise ValueError("preflight section retention requires tool context")
        preflight = getattr(outcome, "preflight", None)
        if preflight is None:
            raise ValueError(
                "preflight section retention requires preflight system prompt sections"
            )
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
    request: RunRequest,
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
    request: RunRequest,
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
    session_store: SessionStore | None,
    run_snapshot_store: RunSnapshotStore | None,
    request: RunRequest,
    tree: SessionTree | None,
    snapshot: RunSnapshot | None,
    tool_ctx: ToolContext | None,
    ports: ContextRuntimePorts,
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
