"""Phase 1 F4: ``reduce_subagent_results`` builtin tool.

Per ``openspec/changes/add-multi-agent-delegate-v15-phase1/specs/builtin-tools/spec.md``
ADDED Requirement "reduce_subagent_results tool merges N parallel sub-agent
results into one message".

The tool collapses N parallel sub-agent text results into a single message
using one of 4 strategies. It is parent-agent-only (sub-agents cannot call
it — see ``subagent-system`` spec). The ``results`` parameter is
``list[str]`` because LLMs can reliably copy ToolResult text into tool
params; preserving ``ToolResult.is_error`` across tool boundaries is not
reliable. ``first_success`` detects errors via PrismClaw-controlled prefixes
(``[子代理执行失败：``, ``[子代理超时``, ``[子代理已被父级中止]``).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Literal

from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.agent.data_types import TextBlock
from prismclaw.schema.content.subagent import SubagentResultEnvelope

logger = logging.getLogger(__name__)

_AUDIT_LOGGER_NAME = "prismclaw.audit.subagent"

ReduceStrategy = Literal[
    "concat",
    "merge_json",
    "merge_envelopes",
    "vote",
    "first_success",
]

_CONCAT_SEPARATOR = "\n\n</result>\n<result>\n\n"
_ALL_FAILED_SEPARATOR = "\n</error_result>\n<error_result>\n"
_FAILED_PREFIXES = (
    "[子代理执行失败：",
    "[子代理超时",
    "[子代理已被父级中止]",
    "[子代理已达最大轮次上限",
)


_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "strategy": {
            "type": "string",
            "enum": [
                "concat",
                "merge_json",
                "merge_envelopes",
                "vote",
                "first_success",
            ],
        },
        "results": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "待归约的纯文本结果。请把每个子代理 ToolResult 的文本内容作为字符串依次传入。"
            ),
        },
    },
    "required": ["strategy", "results"],
    "additionalProperties": False,
}


_DESCRIPTION = (
    "把 N 个并行子代理的文本结果归约为一条消息。"
    "策略：'concat' 以分隔符拼接；'merge_json' 将每项按 JSON 解析后合并为列表"
    "（解析失败时回退为拼接）；"
    "'merge_envelopes' 将每个结果按结构化子代理信封解析，"
    "并对 summary/findings/decisions/open_questions/artifact_refs/"
    "recommended_next_actions 做去重合并（解析失败时回退为拼接）；"
    "'vote' 返回出现次数最多的结果（票数相同则取最先出现者）；"
    "'first_success' 返回第一个非错误结果；若所有结果都带有 PrismClaw 错误前缀，则汇总全部错误。"
    "请把每个子代理的文本内容作为字符串放入 'results' 数组。仅限父代理使用。"
)


class ReduceSubAgentResultsTool:
    name = "reduce_subagent_results"
    side_effect = False
    tool_class = "read"
    parent_only = True

    def __init__(self, audit_logger: logging.Logger | None = None) -> None:
        self.parameters = _PARAMETERS_SCHEMA
        self.description = _DESCRIPTION
        self._audit = audit_logger or logging.getLogger(_AUDIT_LOGGER_NAME)

    async def execute(
        self, args: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        call_id = args.get("_call_id", "")
        strategy = args.get("strategy", "")
        results: list[str] = args.get("results", [])

        if strategy not in (
            "concat",
            "merge_json",
            "merge_envelopes",
            "vote",
            "first_success",
        ):
            return ToolResult(
                tool_call_id=call_id,
                content=[
                    TextBlock(
                        text=(
                            f"reduce_subagent_results：未知策略 "
                            f"{strategy!r}；可选值：concat | merge_json | merge_envelopes | vote | first_success"
                        )
                    )
                ],
                is_error=True,
            )

        n_inputs = len(results)
        text, is_error, strategy_used, fallback_reason = _apply_strategy(
            strategy, results
        )

        self._emit_audit(
            session_id=context.session_id,
            strategy_used=strategy_used,
            n_inputs=n_inputs,
            fallback_reason=fallback_reason,
        )

        return ToolResult(
            tool_call_id=call_id,
            content=[TextBlock(text=text)],
            is_error=is_error,
        )

    def _emit_audit(
        self,
        *,
        session_id: str,
        strategy_used: str,
        n_inputs: int,
        fallback_reason: str | None,
    ) -> None:
        try:
            payload: dict[str, Any] = {
                "event": "subagent_batch_reduced",
                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "session_id": session_id,
                "strategy_used": strategy_used,
                "n_inputs": n_inputs,
            }
            if fallback_reason is not None:
                payload["fallback_reason"] = fallback_reason
            self._audit.info(
                json.dumps(payload, separators=(",", ":"), sort_keys=False)
            )
        except Exception:
            logger.warning("subagent.batch_reduced_audit.emit_failed", exc_info=True)


def _apply_strategy(
    strategy: str, results: list[str]
) -> tuple[str, bool, str, str | None]:
    if strategy == "concat":
        if not results:
            return "", False, "concat", None
        return "<result>\n" + "</result>\n<result>\n".join(results) + "\n</result>", False, "concat", None

    if strategy == "merge_json":
        return _apply_merge_json(results)

    if strategy == "merge_envelopes":
        return _apply_merge_envelopes(results)

    if strategy == "vote":
        return _apply_vote(results)

    if strategy == "first_success":
        return _apply_first_success(results)

    return "", True, strategy, "unknown_strategy"


def _apply_merge_json(
    results: list[str],
) -> tuple[str, bool, str, str | None]:
    parsed: list[Any] = []
    for entry in results:
        try:
            parsed.append(json.loads(entry))
        except (json.JSONDecodeError, ValueError) as exc:
            if not results:
                concat_str = ""
            else:
                concat_str = "<result>\n" + "</result>\n<result>\n".join(results) + "\n</result>"
            return (
                concat_str,
                False,
                "concat",
                f"merge_json_parse_failed: {exc}",
            )
    return json.dumps(parsed, separators=(",", ":")), False, "merge_json", None


def _apply_vote(results: list[str]) -> tuple[str, bool, str, str | None]:
    if not results:
        return "", False, "vote", None
    stripped_to_original: dict[str, str] = {}
    for entry in results:
        key = entry.strip()
        if key not in stripped_to_original:
            stripped_to_original[key] = entry
    counts = Counter(entry.strip() for entry in results)
    max_count = max(counts.values())
    for entry in results:
        if counts[entry.strip()] == max_count:
            return entry, False, "vote", None
    return results[0], False, "vote", None


def _apply_merge_envelopes(
    results: list[str],
) -> tuple[str, bool, str, str | None]:
    envelopes: list[SubagentResultEnvelope] = []
    for entry in results:
        try:
            parsed = json.loads(entry)
            envelopes.append(SubagentResultEnvelope.model_validate(parsed))
        except (json.JSONDecodeError, ValueError) as exc:
            if not results:
                concat_str = ""
            else:
                concat_str = "<result>\n" + "</result>\n<result>\n".join(results) + "\n</result>"
            return (
                concat_str,
                False,
                "concat",
                f"merge_envelopes_parse_failed: {exc}",
            )

    merged = SubagentResultEnvelope(
        summary="\n".join(_dedupe_strings([env.summary for env in envelopes if env.summary.strip()])),
        findings=_dedupe_many(env.findings for env in envelopes),
        decisions=_dedupe_many(env.decisions for env in envelopes),
        open_questions=_dedupe_many(env.open_questions for env in envelopes),
        artifact_refs=_dedupe_many(env.artifact_refs for env in envelopes),
        recommended_next_actions=_dedupe_many(
            env.recommended_next_actions for env in envelopes
        ),
    )
    return json.dumps(merged.model_dump(mode="json"), separators=(",", ":")), False, "merge_envelopes", None


def _apply_first_success(
    results: list[str],
) -> tuple[str, bool, str, str | None]:
    for entry in results:
        if not _is_error_text(entry):
            return entry, False, "first_success", None
    if not results:
        return "", True, "first_success", None
    return "<error_result>\n" + "</error_result>\n<error_result>\n".join(results) + "\n</error_result>", True, "first_success", None


def _is_error_text(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in _FAILED_PREFIXES)


def _dedupe_many(groups: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
    return merged


def _dedupe_strings(values: list[str]) -> list[str]:
    return _dedupe_many([values])


__all__ = ["ReduceSubAgentResultsTool"]
