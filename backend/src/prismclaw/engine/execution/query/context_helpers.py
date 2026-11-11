from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismclaw.engine.context.projection_models import ProjectionMessageSet
from prismclaw.schema.session.types import SessionTree
from prismclaw.schema.agent.session import ProjectionInputMessage

if TYPE_CHECKING:
    from prismclaw.engine.execution.loop.bootstrap import AgentRunnerDeps
    from prismclaw.engine.execution.loop.runner import RunRequest
    from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
    from prismclaw.engine.execution.query.state import (
        QueryLoopState,
        QueryRuntimeContext,
    )


# [Phase 0' Decoupled Trajectory] — capture layer 0.5 and layer ① at the
# projector single call-site.  The capture constructs bus events and
# publishes to the core-owned SessionBus (NOT trajectory-owned aggregator).
#
# CRITICAL ARCHITECTURE:
#   - Core code imports ONLY `prismclaw.engine.bus.current_bus` and
#     `prismclaw.engine.bus_events.*`.  Never imports trajectory.*.
#   - Trajectory audit is a DOWNSTREAM subscriber (TrajectoryAuditService)
#     that registers itself on the bus via core.bus.  It can be completely
#     disabled / uninstalled without touching ONE line of core code here.
#   - publish() NEVER raises (SessionBus swallows all handler exceptions),
#     so a broken trajectory subscriber can't break the agent loop.
#
# Compare to v1.0 which called `from prismclaw.engine.trajectory import
# current_aggregator` inside every capture site (coupled, invasive).
def _trajectory_capture_projector(
    tree: object,
    source_messages: list[dict[str, Any]],
    appended_messages: list[dict[str, Any]] | None = None,
) -> None:
    try:
        from prismclaw.engine.trajectory.bus import current_bus
        from prismclaw.engine.trajectory.bus_events import (
            ProjectorInputDone,
            ProjectorOutputDone,
        )
        from prismclaw.engine.utils.summarize import (
            summarize_messages,
            summarize_tree_order,
        )

        bus = current_bus()
        if bus is None or bus._is_noop:
            # Fast path — bus was never initialised (feature off or CLI).
            return

        # Fetch the iteration identity from the trajectory runtime ctx.
        # Outside a runner loop there is no identity to attribute events
        # to — skip publishing entirely (the bus is per-session, a fake
        # session_id event would only be dropped downstream).
        from prismclaw.engine.trajectory.bus_context import current_runtime  # noqa: PLC0415
        _rt = current_runtime()
        if _rt is None:
            return
        sid, turn_counter, iteration = _rt.session_id, _rt.turn, _rt.iteration

        # Layer 0.5: tree.order entries BEFORE MessageProjector ran
        order_entries = list(getattr(tree, "order", ()) or [])
        order_count, entry_types, head_seq, tail_seq, tail_types = (
            summarize_tree_order(order_entries)
        )
        bus.publish(ProjectorInputDone(
            session_id=sid, turn_counter=turn_counter, iteration=iteration,
            order_count=order_count,
            order_entry_types=entry_types,
            order_head_seq=head_seq,
            order_tail_seq=tail_seq,
            order_tail_types=tail_types,
        ))

        # Layer ① projector output messages
        msg_count, roles, head_summ, tail_summ = summarize_messages(source_messages)
        # Fix F: surface the count of messages that will be injected
        # downstream via `appended_messages_override`. The projector's
        # msg_count is the tree's history branch only — on a fresh turn
        # with empty history this is 0, but the LLM still receives 1+
        # appended messages. Without this field the panel misleadingly
        # shows "Layer ① = 0 messages" while Layers ②/③/④ show 1+.
        _appended_count = len(list(appended_messages or []))

        # DSH parity: extract actual content previews so the panel can
        # show WHAT the user asked and WHAT tools returned, not just
        # "[user] xxx[:80]" snippets.
        #   user_message_preview = last role='user' message text (500 chars)
        #   tool_results_preview  = each role='tool' message text (200 chars)
        _user_preview: str | None = None
        _tool_previews: list[str] = []
        try:
            _all_msgs = list(source_messages or []) + list(appended_messages or [])
            for _m in _all_msgs:
                if not isinstance(_m, dict):
                    continue
                _r = str(_m.get("role") or "")
                _c = _m.get("content")
                if isinstance(_c, list):
                    _c_str = " ".join(
                        str(getattr(_b, "text", "") or _b if not isinstance(_b, str) else _b)
                        for _b in _c
                    )[:200]
                elif isinstance(_c, str):
                    _c_str = _c
                elif _c is None:
                    _c_str = ""
                else:
                    _c_str = str(_c)
                if _r == "user" and _c_str:
                    _user_preview = _c_str[:500]
                elif _r == "tool" and _c_str:
                    _tool_previews.append(_c_str[:200])
        except Exception:
            _user_preview = None
            _tool_previews = []

        bus.publish(ProjectorOutputDone(
            session_id=sid, turn_counter=turn_counter, iteration=iteration,
            msg_count=msg_count,
            msg_roles=roles,
            head=head_summ,
            tail=tail_summ,
            appended_msg_count=_appended_count,
            user_message_preview=_user_preview,
            tool_results_preview=tuple(_tool_previews),
        ))
    except Exception:
        # 🛡️ Never break baseline build — trajectory is diagnostic-only.
        pass


def _build_query_runtime_context(
    *,
    request: "RunRequest",
    deps: "AgentRunnerDeps",
    tool_ctx: "ToolContext",
    effective_model: str,
    attachment_guidance: str | None,
) -> "QueryRuntimeContext":
    from prismclaw.engine.execution.query.state import QueryRuntimeContext

    effective_mode = request.mode_override
    if effective_mode is None:
        effective_mode = tool_ctx.mode
    return QueryRuntimeContext(
        query_source="runner",
        capability_snapshot=deps.capability_catalog,
        tool_context=tool_ctx,
        app_context={
            "session_id": request.session_id,
            "workspace_id": request.workspace_id,
            "agent_id": request.agent_id,
            "request_id": request.request_id,
        },
        budget_policy=deps.config.context.budget,
        permission_context={
            "effective_tier": effective_mode,
            "default_tier": None,
        },
        model_context={
            "effective_model": effective_model,
            "attachment_guidance": attachment_guidance,
        },
        session_hooks=deps.hooks,
    )


def _build_source_query_loop_state(
    *,
    tree: SessionTree,
    request: "RunRequest",
    deps: "AgentRunnerDeps",
    tool_ctx: "ToolContext",
    effective_model: str,
    attachment_guidance: str | None,
    tool_loop_state: dict[str, object],
    pending_meta_messages: list[ProjectionInputMessage] | None,
    appended_messages_override: list[ProjectionInputMessage] | None,
) -> "QueryLoopState":
    from prismclaw.engine.execution.query.builder import build_query_loop_state

    source_messages = tree.build_runtime_messages()
    source_meta_messages = list(tree.build_runtime_meta_messages() or [])

    # ⚓ [Phase 0' Trajectory] layer 0.5 + layer ① capture (single call-site).
    # Must run AFTER source_messages computed, BEFORE source_message_set is
    # copied — otherwise appended_messages_override mutations would leak into
    # projector snapshot (those are Layer ② assemble entries, not projector).
    #
    # Fix F: pass `appended_messages_override` so the projector snapshot can
    # surface appended_msg_count (the count of messages injected downstream
    # via the override path). The projector's msg_count itself stays strict
    # (= tree.build_runtime_messages() output), but appended_msg_count lets
    # the panel show "history=0 + appended=N → total N" instead of a
    # misleading "Layer ① = 0 messages".
    _trajectory_capture_projector(
        tree,
        source_messages,
        appended_messages=appended_messages_override,
    )

    if pending_meta_messages is not None:
        source_meta_messages.extend(pending_meta_messages)
    source_message_set = ProjectionMessageSet(
        messages=source_messages,
        meta_messages=source_meta_messages,
        appended_messages=list(appended_messages_override or []),
    )
    return build_query_loop_state(
        source_messages=source_message_set,
        runtime_context=_build_query_runtime_context(
            request=request,
            deps=deps,
            tool_ctx=tool_ctx,
            effective_model=effective_model,
            attachment_guidance=attachment_guidance,
        ),
        tool_loop_state=tool_loop_state,
        attachment_state={"attachment_guidance": attachment_guidance}
        if attachment_guidance is not None
        else None,
    )


def _post_baseline_stable_prefix_follow_up_turn_count(tree: SessionTree) -> int:
    snapshots = tree.get_cache_strategy_snapshots()
    if len(snapshots) <= 1:
        return 0
    return sum(1 for snapshot in snapshots[1:] if snapshot.get("request_mode") == "follow_up")


def _existing_context_compaction_artifact_keys(tree: SessionTree) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for artifact in tree.get_persisted_artifacts().values():
        if artifact.scope != "context_block":
            continue
        keys.add((artifact.artifact_id, artifact.source_digest or ""))
    return keys