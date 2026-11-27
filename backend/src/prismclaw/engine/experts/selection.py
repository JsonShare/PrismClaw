from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal, TypedDict

from prismclaw.engine.experts.codes import ExpertError, ExpertErrorCode
from prismclaw.engine.experts.loading.load_result import ExpertPackage
from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
from prismclaw.engine.trajectory.bus import current_bus
from prismclaw.engine.trajectory.bus_events import (
    ExpertCancelled,
    ExpertStateChanged,
    ExpertUnavailable,
)
from prismclaw.infra.config.capability_policy import CapabilityPolicyRegistry
from prismclaw.schema.agent.session import SessionHeader
from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.session.types import SessionTree
from prismclaw.schema.storage.storage_types import SessionStore

logger = logging.getLogger(__name__)
_event_logger = SessionEventLogger()


class ExpertState(TypedDict):
    expert_id: str | None
    source: str | None
    selected_skill_ids: list[str]
    # 会话选中的专家此刻能否生效；None 表示可用或未选专家。
    # 与 run 早退使用同一组错误码，前端据此禁用输入框。
    unavailable_reason: str | None


async def select_session_expert(
    session_id: str,
    expert_id: str | None,
    *,
    session_store: SessionStore,
    expert_registry: ExpertRegistry,
    capability_policy_registry: CapabilityPolicyRegistry,
) -> ExpertState:
    if expert_id is None:
        tree = await _load_session(session_id, session_store)
        header = tree.header
        cancelled_expert_id = header.expert_id or header.last_user_prompt_expert_selection
        header_update: dict[str, object] = {
            "expert_id": None,
            "expert_runtime_identity": None,
            "expert_activation_source": "user",
        }
        if header.expert_id:
            header_update["last_user_prompt_expert_selection"] = header.expert_id
        reason = "expert_cancellation"
        expert = None
        kind = None
    else:
        requested_id = expert_id.strip()
        expert = expert_registry.get(requested_id)
        if expert is None:
            code = (
                ExpertErrorCode.EXPERT_UNAVAILABLE
                if _is_listed_but_unavailable(expert_registry, requested_id)
                else ExpertErrorCode.EXPERT_NOT_FOUND
            )
            message = (
                "Expert is listed but unavailable."
                if code == ExpertErrorCode.EXPERT_UNAVAILABLE
                else f"Expert {requested_id!r} is not deployed."
            )
            if code == ExpertErrorCode.EXPERT_UNAVAILABLE:
                tree = await _load_session(session_id, session_store)
                turn = _current_turn(tree)
                current_bus().publish(
                    ExpertUnavailable(
                        session_id=session_id,
                        expert_id=requested_id,
                        code=code,
                        source="user",
                        reason="expert_selection",
                        turn=turn,
                    )
                )
                logger.info(
                    "expert_unavailable",
                    extra={
                        "audit": {
                            "session_id": session_id,
                            "expert_id": requested_id,
                            "source": "user",
                            "reason": "expert_selection",
                            "turn": turn,
                        }
                    },
                )
            raise ExpertError(code, message, {"expert_id": requested_id})
        kind = _expert_kind(expert)
        if not capability_policy_registry.is_enabled(kind, expert.name):
            raise ExpertError(
                ExpertErrorCode.MODULE_DISABLED,
                f"Expert {expert.name!r} is disabled.",
                {"expert_id": expert.name},
            )
        tree = await _load_session(session_id, session_store)
        header = tree.header
        header_update = {
            "expert_id": expert.name,
            "expert_runtime_identity": expert.name,
            "last_user_prompt_expert_selection": expert.name,
            "expert_activation_source": "user",
        }
        reason = "expert_selection"
        cancelled_expert_id = None

    if all(getattr(header, key) == value for key, value in header_update.items()):
        # 选择路径已校验「包存在且策略启用」，此处无需再查。
        return _expert_state(header, unavailable_reason=None)

    committed = await session_store.commit_events(
        [
            _event_logger.request_header(
                session_id,
                header_update,
                reason=reason,
            )
        ]
    )
    updated_header = (await _load_session(session_id, session_store)).header
    turn = _current_turn(tree)
    _publish_state_change(
        session_id=session_id,
        revision=committed[0].seq,
        header=header.model_copy(update=header_update),
        expert=expert,
        kind=kind,
        cancelled_expert_id=cancelled_expert_id,
        reason=reason,
        turn=turn,
    )
    _write_audit(
        session_id=session_id,
        previous_expert_id=header.expert_id,
        expert_id=expert.name if expert is not None else cancelled_expert_id,
        cancelled=expert is None,
        reason=reason,
        turn=turn,
    )
    return _expert_state(updated_header, unavailable_reason=None)


async def get_session_expert_state(
    session_id: str,
    *,
    session_store: SessionStore,
    expert_registry: ExpertRegistry,
    capability_policy_registry: CapabilityPolicyRegistry,
) -> ExpertState:
    tree = await _load_session(session_id, session_store)
    return _expert_state(
        tree.header,
        unavailable_reason=_unavailable_reason(
            tree.header, expert_registry, capability_policy_registry
        ),
    )


def _expert_state(
    header: SessionHeader,
    *,
    unavailable_reason: str | None,
) -> ExpertState:
    return {
        "expert_id": header.expert_id,
        "source": header.expert_activation_source,
        "selected_skill_ids": list(header.selected_skill_ids),
        "unavailable_reason": unavailable_reason,
    }


def _unavailable_reason(
    header: SessionHeader,
    expert_registry: ExpertRegistry,
    capability_policy_registry: CapabilityPolicyRegistry,
) -> str | None:
    """会话当前专家能否生效；不可用时返回与 run 早退一致的原因码。

    run 入口在消息落库前用 ``build_session_scope`` 判同样的事（包能否解析、
    策略是否禁用），此处读同一事实源，避免「界面显示在用、run 一起步就被拒」。
    """
    if not header.expert_id:
        return None
    try:
        expert = expert_registry.get(header.expert_id)
    except ExpertError as exc:
        # 裸名在多来源重名时无法唯一解析：与 run 入口同样按不可用处理。
        return exc.code.value
    if expert is None:
        return ExpertErrorCode.EXPERT_UNAVAILABLE.value
    state = capability_policy_registry.snapshot().states.get(
        (_expert_kind(expert), expert.name)
    )
    if state is not None and not state.enabled:
        return ExpertErrorCode.MODULE_DISABLED.value
    return None


async def _load_session(session_id: str, session_store: SessionStore) -> SessionTree:
    tree = await session_store.load(session_id)
    if tree is None:
        raise LookupError(f"Session {session_id!r} not found.")
    return tree


def _current_turn(tree: SessionTree) -> int:
    user_messages = sum(
        1
        for entry_id in tree.order
        if (entry := tree.entries.get(entry_id)) is not None
        and getattr(entry, "role", None) == "user"
    )
    return max(1, user_messages)


def _is_listed_but_unavailable(registry: ExpertRegistry, expert_id: str) -> bool:
    return registry.issue_for(expert_id) is not None


def _expert_kind(expert: ExpertPackage) -> Literal["expert", "team"]:
    manifest = expert.manifest
    return "team" if manifest is not None and manifest.expert_type == "team" else "expert"


def _publish_state_change(
    *,
    session_id: str,
    revision: int,
    header: SessionHeader,
    expert: ExpertPackage | None,
    kind: Literal["expert", "team"] | None,
    cancelled_expert_id: str | None,
    reason: str,
    turn: int,
) -> None:
    now = datetime.now(UTC).isoformat()
    bus = current_bus()
    bus.publish(
        ExpertStateChanged(
            session_id=session_id,
            revision=revision,
            state="active" if expert is not None else "none",
            expert_id=expert.name if expert is not None else None,
            expert_type="team" if kind == "team" else "agent" if kind == "expert" else None,
            source="user",
            updated_at=now,
            reason=reason,
            turn=turn,
        )
    )
    if expert is None and cancelled_expert_id:
        bus.publish(
            ExpertCancelled(
                session_id=session_id,
                expert_id=cancelled_expert_id,
                source="user",
                reason=reason,
                turn=turn,
            )
        )


def _write_audit(
    *,
    session_id: str,
    previous_expert_id: str | None,
    expert_id: str | None,
    cancelled: bool,
    reason: str,
    turn: int,
) -> None:
    if cancelled:
        logger.info(
            "expert_cancelled",
            extra={
                "audit": {
                    "session_id": session_id,
                    "expert_id": expert_id,
                    "source": "user",
                    "reason": reason,
                    "turn": turn,
                }
            },
        )
        return
    logger.info(
        "expert_switch",
        extra={
            "audit": {
                "session_id": session_id,
                "expert_id": expert_id,
                "source": "user",
                "reason": reason,
                "turn": turn,
                "from": previous_expert_id,
                "to": expert_id,
            }
        },
    )


__all__ = ["ExpertState", "get_session_expert_state", "select_session_expert"]
