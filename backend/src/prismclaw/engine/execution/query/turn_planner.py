"""本轮前置决策的唯一计算点。

`TurnPlanner.plan()` 每轮调用一次，把散落在产物构造函数里的判断
（governance 可见集、是否跳过重管道、是否加载 L1、是否无头）收敛成一份
`TurnPlan`。调用方只消费，不重算（R3：同一决策只允许一个计算点）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismclaw.engine.execution.query.turn_plan import TurnPlan as TurnPlan
from prismclaw.engine.experts.scope import tool_surface_of
from prismclaw.engine.state.turn_state import TurnState
from prismclaw.engine.toolkit.registry.governance import (
    ResolvedToolSet,
    resolve_registry_tool_governance,
)

if TYPE_CHECKING:
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
    from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
    from prismclaw.schema.agent.types import RunRequest
    from prismclaw.schema.services.skills import SkillInvocationState


def _with_frozen_excluded(
    toolset: ResolvedToolSet,
    frozen_tools: frozenset[str] | set[str],
) -> ResolvedToolSet:
    """把本轮熔断工具从可下发集合中剔除。

    熔断（冻结）是本轮循环状态（``turn_prepare`` 在 ``pipeline.run`` 之前写入），
    属于工具面事实。它必须在 planner 内参与定稿，否则 pipeline 会做第二处
    收窄——同一决策两个计算点，壳与实发随时可能分叉（SDD D14 同类缺陷）。
    """
    frozen = frozenset(frozen_tools)
    return toolset.model_copy(
        update={
            "visible_tools_for_llm": tuple(
                name for name in toolset.visible_tools_for_llm if name not in frozen
            ),
            "callable_tools": tuple(name for name in toolset.callable_tools if name not in frozen),
        }
    )


class TurnPlanner:
    """把事实输入折叠成一份 `TurnPlan`。无状态，可安全复用。"""

    def plan(
        self,
        *,
        request: RunRequest,
        deps: AgentRunnerDeps,
        turn_state: TurnState,
        tool_ctx: ToolContext,
        skill_invocation: SkillInvocationState | None,
        frozen_tools: frozenset[str] | set[str] = frozenset(),
    ) -> TurnPlan:
        complexity = getattr(request, "complexity", None)
        is_simple = complexity == "simple"

        # 能否暂停等人回答，取决于是否持有会话队列（当前仅 Web 主循环具备）。
        # CLI / 测试等无队列场景属无头执行，需交互用户的工具不下发给模型。
        # 定时任务复用 Web 主链路，持有队列，因此与普通会话同等待遇。
        # 事实已在 ToolContext 构造时判定一次（R3），此处只读取，不重算。
        headless = bool(getattr(tool_ctx, "headless", False))
        subagent_depth = getattr(tool_ctx, "subagent_depth", 0) or 0

        scope = tool_ctx.session_scope
        toolset = resolve_registry_tool_governance(
            deps.tools,
            skill_invocation=skill_invocation,
            discovered_tools=turn_state.discovered_tool_names(),
            subagent_depth=subagent_depth,
            headless=headless,
            # 作用域事实直传：壳与主链必须同源，否则 <tools> 段会宣传
            # 模型实际调不到的工具（SDD D14）。
            allowed_tool_owners=scope.tool_owners if scope is not None else None,
            tool_surface=tool_surface_of(scope),
        )
        if frozen_tools:
            toolset = _with_frozen_excluded(toolset, frozen_tools)

        return TurnPlan(
            complexity=complexity,
            toolset=toolset,
            # 减法：simple（RunPreparer 规则评估写入）跳过 microcompact /
            # file_reinjection 等对短上下文无意义的重步骤；只跳过管道步骤，
            # 不裁剪工具集（关键词打分不足以判断是否需要写工具）。
            skip_history_compaction=is_simple,
            # L1 记忆索引在 simple 轮次维持零 IO，不加载。
            include_l1_memory=not is_simple,
            headless=headless,
        )
