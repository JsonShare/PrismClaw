"""Hook 输入/输出 Schema。

对齐参考实现 ``hooks.ts`` 的 HookInput/HookOutput 结构。

参考实现：
- ``createBaseHookInput`` (L220) → HookContext
- ``HookResult`` (L260) + ``processHookJSONOutput`` (L500) → HookResult
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class HookOutcome(str, Enum):
    """Hook 执行结果。对齐参考实现 outcome。"""

    SUCCESS = "success"
    BLOCKING = "blocking"
    NON_BLOCKING_ERROR = "non_blocking_error"
    CANCELLED = "cancelled"


class PermissionDecision(str, Enum):
    """PreToolUse 权限决策。对齐参考实现 ``permissionDecision``。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class HookDecision(str, Enum):
    """通用决策。对齐参考实现 ``decision``。"""

    APPROVE = "approve"
    BLOCK = "block"


@dataclass(slots=True)
class HookContext:
    """Hook 输入。对齐参考实现 ``createBaseHookInput`` (L220)。"""

    event: str
    session_id: str
    transcript_path: str = ""
    cwd: str = ""
    permission_mode: str | None = None
    agent_id: str | None = None
    agent_type: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: Any | None = None  # POST_TOOL：工具输出文本（供 hook 改写）
    skill_name: str | None = None
    plugin_id: str | None = None
    plugin_root: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 可传输的字典（用于 shell command hook stdin）。"""
        result: dict[str, Any] = {
            "event": self.event,
            "session_id": self.session_id,
            "cwd": self.cwd,
        }
        if self.transcript_path:
            result["transcript_path"] = self.transcript_path
        if self.permission_mode:
            result["permission_mode"] = self.permission_mode
        if self.agent_id:
            result["agent_id"] = self.agent_id
        if self.agent_type:
            result["agent_type"] = self.agent_type
        if self.tool_name:
            result["tool_name"] = self.tool_name
        if self.tool_input is not None:
            result["tool_input"] = self.tool_input
        if self.tool_output is not None:
            result["tool_output"] = self.tool_output
        if self.skill_name:
            result["skill_name"] = self.skill_name
        if self.plugin_id:
            result["plugin_id"] = self.plugin_id
        return result


@dataclass(slots=True)
class HookResult:
    """Hook 输出。

    对齐参考实现 ``hooks.ts`` ``HookResult`` (L260) +
    ``processHookJSONOutput`` (L500)。

    关键字段对标参考实现 ``hookJSONOutputSchema``：
    - outcome: 对标 outcome
    - decision: 对标 decision ('approve'|'block')
    - permission_decision: 对标 hookSpecificOutput.permissionDecision
    - updated_input: 对标 hookSpecificOutput.updatedInput (PreToolUse 修改工具参数)
    - additional_context: 对标 hookSpecificOutput.additionalContext
    - system_message: 对标 systemMessage
    - stop_reason: 对标 stopReason
    - watch_paths: 对标 hookSpecificOutput.watchPaths (SessionStart)
    """

    outcome: HookOutcome = HookOutcome.SUCCESS
    continue_execution: bool = True
    decision: HookDecision | None = None
    decision_reason: str | None = None
    permission_decision: PermissionDecision | None = None
    permission_decision_reason: str | None = None
    updated_input: dict[str, Any] | None = None
    additional_context: str | None = None
    updated_mcp_tool_output: Any | None = None
    system_message: str | None = None
    stop_reason: str | None = None
    watch_paths: list[str] | None = None
    initial_user_message: str | None = None
    retry: bool | None = None
    suppress_output: bool = False

    @classmethod
    def success(cls) -> "HookResult":
        return cls()

    @classmethod
    def blocking(cls, *, reason: str | None = None) -> "HookResult":
        return cls(
            outcome=HookOutcome.BLOCKING,
            continue_execution=False,
            decision=HookDecision.BLOCK,
            decision_reason=reason,
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HookResult":
        """从 JSON 字典解析 HookResult。

        对标参考实现 ``processHookJSONOutput`` (L500)。
        """
        outcome_raw = raw.get("outcome", "success")
        try:
            outcome = HookOutcome(outcome_raw)
        except ValueError:
            outcome = HookOutcome.SUCCESS

        decision_raw = raw.get("decision")
        decision: HookDecision | None = None
        if decision_raw in ("approve", "block"):
            decision = HookDecision(decision_raw)

        continue_execution = raw.get("continue", True)
        if decision == HookDecision.BLOCK:
            continue_execution = False

        # hookSpecificOutput 对标参考实现 L500+
        specific = raw.get("hookSpecificOutput", {})
        if not isinstance(specific, dict):
            specific = {}

        perm_raw = specific.get("permissionDecision") or raw.get("permissionDecision")
        permission_decision: PermissionDecision | None = None
        if perm_raw in ("allow", "deny", "ask"):
            permission_decision = PermissionDecision(perm_raw)

        return cls(
            outcome=outcome,
            continue_execution=continue_execution,
            decision=decision,
            decision_reason=raw.get("reason") or raw.get("decisionReason"),
            permission_decision=permission_decision,
            permission_decision_reason=(
                specific.get("permissionDecisionReason") or raw.get("permissionDecisionReason")
            ),
            updated_input=specific.get("updatedInput") or raw.get("updatedInput"),
            additional_context=(
                specific.get("additionalContext") or raw.get("additionalContext")
            ),
            updated_mcp_tool_output=(
                specific.get("updatedMCPToolOutput") or raw.get("updatedMCPToolOutput")
            ),
            system_message=raw.get("systemMessage") or raw.get("system_message"),
            stop_reason=raw.get("stopReason") or raw.get("stop_reason"),
            watch_paths=specific.get("watchPaths") or raw.get("watchPaths"),
            initial_user_message=(
                specific.get("initialUserMessage") or raw.get("initialUserMessage")
            ),
            retry=specific.get("retry") or raw.get("retry"),
            suppress_output=bool(raw.get("suppressOutput", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "outcome": self.outcome.value,
            "continue": self.continue_execution,
        }
        if self.decision is not None:
            result["decision"] = self.decision.value
        if self.decision_reason is not None:
            result["reason"] = self.decision_reason
        if self.permission_decision is not None:
            result["permissionDecision"] = self.permission_decision.value
        if self.permission_decision_reason is not None:
            result["permissionDecisionReason"] = self.permission_decision_reason
        if self.updated_input is not None:
            result["updatedInput"] = self.updated_input
        if self.additional_context is not None:
            result["additionalContext"] = self.additional_context
        if self.system_message is not None:
            result["systemMessage"] = self.system_message
        if self.stop_reason is not None:
            result["stopReason"] = self.stop_reason
        if self.watch_paths is not None:
            result["watchPaths"] = self.watch_paths
        if self.suppress_output:
            result["suppressOutput"] = True
        return result
