from __future__ import annotations

from collections.abc import Iterable

from prismclaw.engine.experts.codes import ExpertError, ExpertErrorCode
from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
from prismclaw.infra.config.capability_policy import CapabilityPolicyRegistry
from prismclaw.runtime.skills.eligibility import is_user_visible
from prismclaw.schema.agent.session import SessionHeader
from prismclaw.schema.services.skills import qualified_skill_id
from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.storage.storage_types import SessionStore

_event_logger = SessionEventLogger()
MAX_SELECTED_SKILLS = 20


async def replace_session_skill_selection(
    session_id: str,
    skill_ids: Iterable[str],
    *,
    session_store: SessionStore,
    expert_registry: ExpertRegistry,
    capability_policy_registry: CapabilityPolicyRegistry,
) -> SessionHeader:
    selected = list(dict.fromkeys(skill_ids))
    if len(selected) > MAX_SELECTED_SKILLS:
        raise ExpertError(
            ExpertErrorCode.SKILL_SELECTION_LIMIT,
            f"At most {MAX_SELECTED_SKILLS} skills can be selected.",
            {"limit": MAX_SELECTED_SKILLS, "count": len(selected)},
        )
    known_skill_ids = set(expert_registry.known_ids("skill"))
    for skill_id in selected:
        if skill_id not in known_skill_ids:
            raise ExpertError(
                ExpertErrorCode.EXPERT_NOT_FOUND,
                f"Skill {skill_id!r} is not deployed.",
                {"skill_id": skill_id},
            )
        if not capability_policy_registry.is_enabled("skill", skill_id):
            raise ExpertError(
                ExpertErrorCode.MODULE_DISABLED,
                f"Skill {skill_id!r} is disabled.",
                {"skill_id": skill_id},
            )
        package_name = skill_id.partition(":")[0]
        for expert in expert_registry.list_experts():
            if expert.name != package_name:
                continue
            manifest = expert.manifest
            kind = "team" if manifest is not None and manifest.expert_type == "team" else "expert"
            if not capability_policy_registry.is_enabled(kind, expert.name):
                raise ExpertError(
                    ExpertErrorCode.MODULE_DISABLED,
                    f"Skill {skill_id!r} belongs to disabled expert {expert.name!r}.",
                    {"skill_id": skill_id, "expert_id": expert.name},
                )
            for skill in expert.skills:
                if qualified_skill_id(expert.name, skill.name) == skill_id and not is_user_visible(
                    skill
                ):
                    raise ExpertError(
                        ExpertErrorCode.MODULE_DISABLED,
                        f"Skill {skill_id!r} is not user-invocable.",
                        {"skill_id": skill_id, "invocation_policy": skill.invocation_policy.value},
                    )
            break
    tree = await session_store.load(session_id)
    if tree is None:
        raise LookupError(f"Session {session_id!r} not found.")
    header_update = {"selected_skill_ids": selected}
    await session_store.commit_events(
        [
            _event_logger.request_header(
                session_id,
                header_update,
                reason="skill_selection",
            )
        ]
    )
    return tree.header.model_copy(update=header_update)


__all__ = ["replace_session_skill_selection"]
