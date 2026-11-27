from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable

from prismclaw.engine.experts.codes import ExpertErrorCode
from prismclaw.schema.agent.session import SessionHeader

logger = logging.getLogger(__name__)

ROLE_OVERRIDE = (
    "**Role Override:** The following expert role definition takes precedence over any "
    "previously established persona or identity context. If there is a conflict between "
    "the role below and any earlier self-description, defer to what is defined here — "
    "this is your active, authoritative role for this conversation."
)
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n?(.*)\Z", re.DOTALL)

ResolveExpertPrompt = Callable[..., Awaitable[str | None]]


def strip_expert_agent_frontmatter(text: str) -> str:
    """Remove leading YAML frontmatter, preserving the trimmed Markdown body."""
    match = _FRONTMATTER_RE.match(text)
    return (match.group(1) if match else text).strip()


def build_expert_agent_prompt(agent_md: str) -> str:
    body = strip_expert_agent_frontmatter(agent_md)
    return f"{ROLE_OVERRIDE}\n\n{body}" if body else ""


def emit_event(code: ExpertErrorCode, **payload: object) -> None:
    """Publish an expert-prompt degradation through the session-scoped bus."""
    from prismclaw.engine.trajectory.bus import current_bus
    from prismclaw.engine.trajectory.bus_events import ExpertPromptDegraded

    current_bus().publish(ExpertPromptDegraded(code=code, **payload))


class ExpertPromptSlotCollector:
    def __init__(self, resolve_expert_prompt: ResolveExpertPrompt) -> None:
        self._resolve = resolve_expert_prompt

    async def collect(self, session: SessionHeader) -> str:
        expert_id = (session.expert_id or "").strip()
        if not expert_id:
            return ""
        try:
            agent_md = await self._resolve(
                expert_id=expert_id,
                expert_name_hint=session.expert_runtime_identity,
            )
        except Exception as exc:
            from prismclaw.engine.trajectory.bus import current_bus
            from prismclaw.engine.trajectory.bus_events import ExpertUnavailable

            reason = str(exc)
            emit_event(
                ExpertErrorCode.EXPERT_UNAVAILABLE,
                session_id=session.id,
                expert_id=expert_id,
                detail=reason,
            )
            current_bus().publish(
                ExpertUnavailable(
                    session_id=session.id,
                    expert_id=expert_id,
                    code=ExpertErrorCode.EXPERT_UNAVAILABLE,
                    source=session.expert_activation_source,
                    reason=reason,
                )
            )
            logger.info(
                "expert_unavailable",
                extra={
                    "audit": {
                        "session_id": session.id,
                        "expert_id": expert_id,
                        "source": session.expert_activation_source,
                        "reason": reason,
                        "turn": None,
                    }
                },
            )
            logger.warning(
                "%s: expert persona unavailable expert_id=%s: %s",
                ExpertErrorCode.EXPERT_UNAVAILABLE,
                expert_id,
                exc,
                exc_info=True,
            )
            return ""
        return build_expert_agent_prompt(agent_md) if agent_md else ""


__all__ = [
    "ROLE_OVERRIDE",
    "ExpertPromptSlotCollector",
    "build_expert_agent_prompt",
    "emit_event",
    "strip_expert_agent_frontmatter",
]
