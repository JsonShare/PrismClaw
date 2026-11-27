"""动态 advisory：领域规则。

边界：子代理的编排 / 证据语义规则归属固定段 ``<subagents>``
（见 ``system_prompt.subagents_section``）——本模块不再重复。这里只保留
``domain_advisory``：领域 bundle 注册的合规规则（如域自定义免责声明）。
"""

from __future__ import annotations

from prismclaw.engine.execution.request._disclaimer_registry import get_advisory_rules
from prismclaw.engine.execution.request._prompt_types import _wrap_xml


def domain_advisory_section() -> str | None:
    """领域合规规则；未注册任何规则时不渲染。"""
    rules = get_advisory_rules()
    if not rules:
        return None
    lines = [f"{i}. **{rule.title}**. {rule.body}" for i, rule in enumerate(rules, start=1)]
    return _wrap_xml("domain_advisory", "\n".join(lines))
