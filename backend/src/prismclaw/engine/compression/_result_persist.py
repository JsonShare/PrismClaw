from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from prismclaw.engine.foundation.tool_output import truncate_tool_result
from prismclaw.engine.compression._helpers import _result_text, _result_text_len
from prismclaw.schema.agent.data_types import TextBlock
from prismclaw.schema.content.content_types import ToolResult

_PERSISTED_PATH_RE = re.compile(r"\[Full output persisted to: (.+?)\]")


def _tool_obj_for_call(call: dict[str, Any] | None, deps: Any) -> Any:
    if not call:
        return None
    tool_name = ((call or {}).get("function") or {}).get("name") or ""
    tools = getattr(deps, "tools", None)
    if tools is None:
        return None
    getter = getattr(tools, "get", None)
    if callable(getter):
        return getter(tool_name)
    return None


def _tool_allows_persist(tool_obj: Any) -> bool:
    """Whether a tool's results may be persisted to disk.

    Mirrors the reference implementation's anti-loop rule: tools that read files back
    (``tool_class == "read"``, e.g. Read/Grep) must never be persisted —
    the model can already re-read the source directly, and persisting
    would add a pointless hop (or, for Read, a read→persist→read loop).
    That matches the reference implementation's ``FileRead`` using ``maxResultSizeChars =
    Infinity``. ``never_persist_output`` is an explicit opt-out for any
    other tool.
    """
    if tool_obj is None:
        return True
    if getattr(tool_obj, "never_persist_output", False):
        return False
    if getattr(tool_obj, "tool_class", None) == "read":
        return False
    moc = getattr(tool_obj, "max_output_chars", None)
    if moc is not None and (moc == float("inf") or moc <= 0):
        return False
    return True


def _persist_reference(
    *,
    result: ToolResult,
    call: dict[str, Any] | None,
    deps: Any,
    working_dir: str | Path | None,
    reason: str,
) -> ToolResult | None:
    """Persist ``result`` to ``{working_dir}/tool_results`` and return a
    ToolResult whose content is the ``<persisted-output>`` reference.

    Returns ``None`` when persistence is not applicable (no working dir,
    the tool forbids it, empty text, or the write fails) so callers can
    fall back to inline truncation / preview.
    """
    if working_dir is None:
        return None
    if not _result_text(result):
        return None
    if not _tool_allows_persist(_tool_obj_for_call(call, deps)):
        return None
    tool_name = ((call or {}).get("function") or {}).get("name") or ""
    reference = deps.ports.persist_tool_result_sync(
        tool_use_id=result.tool_call_id,
        content=result.content,
        reason=reason,
        working_dir=working_dir,
        tool_name=tool_name,
    )
    if reference is None:
        return None
    return ToolResult(
        tool_call_id=result.tool_call_id,
        content=[TextBlock(text=reference)],
        is_error=result.is_error,
        metadata={**(result.metadata or {}), "persisted_output": True},
    )


def _persist_precompression_original(
    *,
    result: ToolResult,
    working_dir: str | Path | None,
    deps: Any,
) -> str | None:
    """SDD T-4: persist the pre-lossy-compression original of an
    un-persisted result; return a one-line recovery pointer.

    The compressed inline text stays (it is semantically useful), but the
    dropped middle lines become recoverable via the read tool. Returns
    ``None`` when persistence isn't possible — the lossy compression then
    proceeds without a recovery copy (best effort, logged upstream).
    """
    if working_dir is None:
        return None
    reference = deps.ports.persist_tool_result_sync(
        tool_use_id=result.tool_call_id,
        content=result.content,
        reason="precompress_persist",
        working_dir=working_dir,
        tool_name="",
    )
    if reference is None:
        return None
    match = _PERSISTED_PATH_RE.search(reference)
    path = match.group(1) if match else "tool_results"
    return f"[压缩前的完整输出已保存至：{path}——可用 read 工具恢复被丢弃的行]"


def _materialize_single_result(
    *,
    result: ToolResult,
    inline_limit: int,
    working_dir: str | Path | None,
    deps: Any,
    call: dict[str, Any] | None = None,
) -> tuple[ToolResult, list[str]]:
    """Single source of truth for how a tool result is materialized.

    逐级披露（progressive disclosure）— 四级决策：

      * ``keep``     — 未超 ``inline_limit``：原样内联。
      * ``persist``  — 超限且可落盘（有 working_dir 且工具允许持久化）：
                       写入 ``{working_dir}/tool_results``，内联替换为
                       ``<persisted-output>`` 引用，模型可随时用 Read
                       工具读回全文。内容零丢失。
      * ``tmpdir_persist`` — 无 working_dir 但可写 temp 目录：写入系统临时目录，
                       仍提供 <persisted-output> 引用。跨会话可能失效但当前会话可恢复。
      * ``truncate`` — 最后手段（工具禁止持久化 / 落盘失败）：head/tail 内联截断并附恢复标记。
    """
    raw_text = _result_text(result)
    original_chars = len(raw_text)

    if original_chars <= inline_limit:
        return result, []

    # Path 1: Persist to working_dir (preferred — survives session restart)
    if working_dir is not None and _tool_allows_persist(
        _tool_obj_for_call(call, deps)
    ):
        persisted = _persist_reference(
            result=result,
            call=call,
            deps=deps,
            working_dir=working_dir,
            reason="per_result_persist",
        )
        if persisted is not None:
            return persisted, ["per_result_persist"]

    # Path 2: Fallback to temp directory (better than truncation)
    if _tool_allows_persist(_tool_obj_for_call(call, deps)):
        import tempfile
        tmp_dir = Path(tempfile.gettempdir()) / "prismclaw_tool_results"
        persisted = _persist_reference(
            result=result,
            call=call,
            deps=deps,
            working_dir=tmp_dir,
            reason="per_result_persist_tmp",
        )
        if persisted is not None:
            return persisted, ["per_result_persist_tmp"]

    # Path 3: Truncation (last resort) — include tool name + size for recovery
    compacted = truncate_tool_result(result, inline_limit)
    tool_name = ((call or {}).get("function") or {}).get("name", "unknown")
    truncated_text = _result_text(compacted)
    if truncated_text and len(truncated_text) < original_chars:
        recovery_note = (
            f"\n\n<!-- 工具结果已截断：{tool_name}（{original_chars} 字符）。"
            f"如需完整输出，请重新运行 {tool_name} 工具。 -->"
        )
        compacted = ToolResult(
            tool_call_id=compacted.tool_call_id,
            content=[TextBlock(text=truncated_text + recovery_note)],
            is_error=compacted.is_error,
            metadata=dict(compacted.metadata or {}),
        )
    strategies: list[str] = []
    if _result_text_len(compacted) < original_chars:
        strategies.append("per_result_truncate")
    return compacted, strategies
