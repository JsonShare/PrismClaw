"""动态 advisory：移交协议变体 + 领域规则。

边界：子代理的编排 / 移交 / 证据语义规则归属固定段 ``<subagents>``
（见 ``system_prompt.subagents_section``）——本模块不再重复。这里只保留
两类内容：

- ``handoff_advisory``：移交协议格式与链长约束（随运行时状态变化）；
- ``domain_advisory``：领域 bundle 注册的合规规则（如医疗免责声明）。
"""

from __future__ import annotations

from prismclaw.engine.execution.handoff.contract import (
    handoff_request_marker_contract_summary,
)
from prismclaw.engine.execution.request._disclaimer_registry import get_advisory_rules
from prismclaw.engine.execution.request._prompt_types import PromptInputs, _wrap_xml


_HANDOFF_ADVISORY_TEXT = (
    "## 跨代理移交\n"
    "子代理可能发出 request_handoff；ToolResult 的 final_text 会以 "
    f"{handoff_request_marker_contract_summary()} 结尾。"
    "按 `<subagents>` 的移交规则处理：派发建议的 sub_agent、用 "
    "reduce_subagent_results 合并，或自行处理并保留 handoff_chain_id。"
)

_HANDOFF_DISABLED_ADVISORY_TEXT = (
    "## 跨代理移交（受管理限制）\n"
    "移交链长限制为 1——request_handoff 在派发时会失败。"
    "不要尝试派发建议的 sub_agent。把子代理视为终点：收到 [HANDOFF_REQUEST] 块时，直接作答。"
)


def handoff_advisory_section(inputs: PromptInputs) -> str | None:
    """移交协议变体；无子代理或未开启 handoff 时不渲染。"""
    if inputs.subagent_registry_size <= 0:
        return None
    handoff_active = inputs.handoff_enabled or inputs.any_manifest_has_can_handoff_to
    if not handoff_active:
        return None
    text = (
        _HANDOFF_ADVISORY_TEXT
        if inputs.max_handoff_chain_length >= 2
        else _HANDOFF_DISABLED_ADVISORY_TEXT
    )
    return _wrap_xml("handoff_advisory", text)


def domain_advisory_section() -> str | None:
    """领域合规规则；未注册任何规则时不渲染。"""
    rules = get_advisory_rules()
    if not rules:
        return None
    lines = [f"{i}. **{rule.title}**. {rule.body}" for i, rule in enumerate(rules, start=1)]
    return _wrap_xml("domain_advisory", "\n".join(lines))
