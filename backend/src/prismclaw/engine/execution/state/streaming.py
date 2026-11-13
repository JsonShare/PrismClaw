from __future__ import annotations

import logging
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismclaw.engine.security.gate.governance import (
    build_initial_task_spec,
    build_review_requirements,
    build_task_context_contract,
    build_turn_semantics,
)
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.session.types import SessionTree

if TYPE_CHECKING:
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
    from prismclaw.schema.agent.types import RunRequest

logger = logging.getLogger(__name__)


async def _apply_fresh_governance_fields(
    *,
    deps: "AgentRunnerDeps",
    request: "RunRequest",
    tree: SessionTree,
    tool_workspace_path: str | PathLike[str] | None,
) -> TaskSpec:
    """Build/refresh all governance fields on ``request``.

    Single source of truth for the governance-field build sequence at turn entry.

    Guarantees that the build stays consistent across rounds:
      * workspace-aware preflight probe (``workspace_path`` always forwarded)
      * task_id continuity across rebuilds (existing ``request.task_id`` wins)
    Returns the reconciled ``TaskSpec`` (also assigned to ``request.task_spec``).
    """
    task_spec = await build_initial_task_spec(
        session_id=request.session_id,
        user_message=request.user_message,
        tree=tree,
        attachments_present=bool(request.attachments),
        mode_override=request.mode_override,
        workspace_path=tool_workspace_path,
    )
    request.task_id = request.task_id or task_spec.task_id
    if request.task_id != task_spec.task_id:
        task_spec = task_spec.model_copy(update={"task_id": request.task_id})
    request.task_spec = task_spec
    request.turn_semantics = build_turn_semantics(
        user_message=request.user_message,
        tree=tree,
    )
    request.task_context_contract = build_task_context_contract(
        task_spec=task_spec,
        turn_semantics=request.turn_semantics,
    )
    request.review_requirements = build_review_requirements(
        task_context_contract=request.task_context_contract,
    )
    # §4.10 动态层：任务级技能路由。**仅会话首轮**（turn_relation=new）触发——
    # 实测延续消息（"改成表格"→3 个无关 workflow）与真实短任务（"做个PPT"=3分）
    # 的 BM25 分数分布重叠，置信度阈值无法区分二者，词汇重叠不反映任务语义；
    # 轮次是唯一可靠信号。首轮是技能发现价值最高点（模型零任务上下文）；
    # 延续轮/会话中途新任务的发现由静态层兜底（项目技能全量 + marketplace
    # 包索引永不截断 + skill_search）。
    # 三分支：唯一命中留给 _try_auto_activate_skill 自动激活；多命中写 hints
    # 供动态段提示；零命中/异常不产出。
    if request.turn_semantics is not None and request.turn_semantics.turn_relation == "new":
        request.skill_route_hints = _route_skill_hints(
            user_message=request.user_message,
            capability_catalog=getattr(deps, "capability_catalog", None),
            workspace_path=tool_workspace_path,
        )
    return task_spec


def _route_skill_hints(
    *,
    user_message: str,
    capability_catalog: Any,
    workspace_path: str | PathLike[str] | None,
) -> tuple[str, ...]:
    """检索当前任务消息，产出 top-3 候选技能的"名称 — 描述"提示行。

    仅在多命中（2-3 个）时产出：唯一命中由自动激活路径接管，零命中无
    推荐。任何检索异常返回空——路由是 best-effort 引导，不阻塞回合。
    """
    if capability_catalog is None or not workspace_path:
        return ()
    text = (user_message or "").strip()
    if not text:
        return ()
    try:
        from prismclaw.engine.execution.capabilities.discovery_runtime import (
            SkillDiscoveryRuntime,
        )

        discovery = SkillDiscoveryRuntime(capability_catalog=capability_catalog)
        matches = discovery.search(text, workspace_path=Path(workspace_path), limit=3)
    except Exception:
        logger.debug("skill_route_hints: search failed (best-effort)", exc_info=True)
        return ()
    if len(matches) < 2:
        return ()
    hints: list[str] = []
    for m in matches[:3]:
        desc = (getattr(m, "description", "") or "").strip()
        hints.append(f"{m.name} — {desc}" if desc else m.name)
    return tuple(hints)