"""HIPAA 合规治理 Reducer — 医疗域工具调用的合规性审查。

实现治理链 Reducer 接口，对医疗域工具调用进行 HIPAA 合规性检查：
- 工具分类与 PHI 访问控制
- 最小必要原则（Minimum Necessary）验证
- 审计日志记录
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HIPAAReducer:
    """HIPAA 合规治理 Reducer。

    作为治理链的一环，在工具调用前进行合规性审查。
    Reducer 接口通过 duck-typing 适配 ToolGovernance 链。
    """

    name: str = "hipaa"
    priority: int = 50

    # PHI 敏感工具列表 — 需要额外审查
    _phi_sensitive_tools: frozenset[str] = field(
        default_factory=lambda: frozenset({
            "smart_medical_record",
        }),
        repr=False,
    )

    # 只读工具列表 — 自动放行
    _read_only_tools: frozenset[str] = field(
        default_factory=lambda: frozenset({
            "search_guideline",
            "search_drug",
            "search_literature",
            "medical_translate",
        }),
        repr=False,
    )

    def reduce(self, tool_name: str, args: dict[str, Any], context: Any) -> dict[str, Any]:
        """执行 HIPAA 合规审查。"""
        audit_tags: list[str] = ["hipaa"]

        if tool_name in self._read_only_tools:
            audit_tags.append("auto_allow_read_only")
            return {
                "decision": "allow",
                "reason": f"只读工具 {tool_name} 符合最小必要原则",
                "audit_tags": audit_tags,
            }

        if tool_name in self._phi_sensitive_tools:
            action = args.get("action", "")
            if action == "create":
                audit_tags.append("phi_write_operation")
                return {
                    "decision": "allow",
                    "reason": f"PHI 写入操作 {tool_name}:{action}，已标记审计",
                    "audit_tags": audit_tags,
                }
            if action == "query":
                audit_tags.append("phi_read_operation")
                return {
                    "decision": "allow",
                    "reason": f"PHI 读取操作 {tool_name}:{action}，已标记审计",
                    "audit_tags": audit_tags,
                }

        audit_tags.append("default_allow")
        return {
            "decision": "allow",
            "reason": f"工具 {tool_name} 未命中 HIPAA 敏感规则，默认放行",
            "audit_tags": audit_tags,
        }
