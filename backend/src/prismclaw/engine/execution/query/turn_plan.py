"""本轮前置决策的唯一载体。

`TurnPlan` 是 `facts → plan → artifacts` 链条里的 `plan`：由 `TurnPlanner`
每轮构造一次，静态壳、工具面、上下文装配共同消费同一份决策，避免每个
产物构造函数各自重算（见 SDD-ARCH-001 §4.1/§4.2）。
"""

from __future__ import annotations

from dataclasses import dataclass

from prismclaw.engine.toolkit.registry.governance import ResolvedToolSet
from prismclaw.schema.agent.task_runtime import TaskComplexity


@dataclass(frozen=True, slots=True)
class TurnPlan:
    """本轮前置决策。构造一次，多处共享，不参与任何输出字节的计算。

    字段取舍（SDD §4.2，R2 标准：只放无法从既有字段推导的信息）：

    - ``toolset``：governance 五道 reducer 的结果，壳 / 工具面 / 审计共用。
    - ``skip_history_compaction``：历史重管道（microcompact / file reinjection）
      是否跳过。与 ``include_l1_memory`` 语义不同，将来可能分别取值。
    - ``include_l1_memory``：是否加载 L1 记忆索引（动态区）。
    - ``complexity``：本轮复杂度评估值。它是 ``skip_history_compaction`` 与
      ``include_l1_memory`` 的来源，并用于日志溯源；但它**不得**触及静态壳
      ——壳是 ``(workspace 事实, model)`` 的纯函数（R1）。
    - ``headless``：governance 输入，显式化以免日志口径与实参漂移。

    ``tool_scope`` 不在此处：它是 ``(tool_ctx, 插件激活状态)`` 的事实输入，
    不是决策输出，作为 ``TurnPlanner.plan()`` 的入参直传 governance。
    """

    complexity: TaskComplexity | None
    toolset: ResolvedToolSet
    skip_history_compaction: bool
    include_l1_memory: bool
    headless: bool
