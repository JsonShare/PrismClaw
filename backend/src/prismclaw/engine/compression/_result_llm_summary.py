from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from prismclaw.engine.compression._compact_prompt import _strip_md_frontmatter
from prismclaw.engine.foundation.metrics import emit

if TYPE_CHECKING:
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
    from prismclaw.schema.config.config import PerResultSummaryConfig

logger = logging.getLogger(__name__)

# Counters / histograms for per-result LLM summarization.
# ``per_result_summary.invoked`` (counter, by tool_name)
# ``per_result_summary.original_chars`` (histogram)
# ``per_result_summary.compacted_chars`` (histogram)
# ``per_result_summary.fallback_to_truncate`` (counter, LLM-failed)
_METRIC_PRS_INVOKED = "per_result_summary.invoked"
_METRIC_PRS_ORIG = "per_result_summary.original_chars"
_METRIC_PRS_COMPACTED = "per_result_summary.compacted_chars"
_METRIC_PRS_FALLBACK = "per_result_summary.fallback_to_truncate"

# Lightweight prompt template for the per-result LLM summary.  Kept
# short (3 sections: goal / key_findings / next_action) because the
# caller has already paid the cost of producing the full result and
# we do not want the LLM to take a long time to summarize it.  The
# identifier-preservation block is appended to keep file paths and
# error codes intact.
P2_2_PER_RESULT_PROMPT = (
    "你正在总结单条工具结果，请保持简洁。\n"
    "\n"
    "按以下顺序返回三个部分：\n"
    "1. 目标：该工具试图完成什么？\n"
    "2. 关键发现：1–5 条简短要点，列出最重要的事实。\n"
    "3. 下一步：下一步应当做什么（若结果已自洽，则写“无”）。\n"
    "\n"
    "不要调用任何工具。不要逐字重复输入内容。\n"
    "逐字保留标识符：文件路径、函数名、"
    "变量名、错误码以及 UUID/哈希。\n"
)


def _load_per_result_prompt() -> str:
    """Load the on-disk ``per_result_summary.md``, stripping its HTML
    comment header before it reaches the LLM; fall back to the inline
    constant when the file is unreadable.

    The header is operator-facing metadata only.  The remaining body is
    byte-identical (after ``.strip()``) to ``P2_2_PER_RESULT_PROMPT``, so
    the file-present and file-missing paths deliver the same instructions.
    This is an intentional 520→167 model-visible-content change; the
    result-scope golden is kept separate from the history golden (§8.7).
    """
    candidate = Path(__file__).resolve().parent / "prompts" / "per_result_summary.md"
    try:
        return _strip_md_frontmatter(candidate.read_text(encoding="utf-8"))
    except OSError:
        return P2_2_PER_RESULT_PROMPT


def _resolve_per_result_summary(deps: AgentRunnerDeps) -> PerResultSummaryConfig | None:
    """Return the per-result summary config, or None when the
    feature is disabled or unavailable.

    Sits on the ``compaction`` block (alongside ``soft_budget``,
    ``compact_prompt``) — same place ``deps.config.context.compaction`` is
    read everywhere else in the pipeline.
    """
    # pydantic schema 保证 deps.config.context.compaction.tool_results
    # .per_result_summary 链齐全（PerResultSummaryConfig 实例，非 Optional）。
    prs = deps.config.context.compaction.tool_results.per_result_summary
    if not bool(prs.enabled):
        return None
    return prs


def _per_result_should_skip(
    *,
    original_chars: int,
    tool_name: str,
    material_type: str,
    prs: PerResultSummaryConfig,
) -> str | None:
    """Gate check.  Returns a reason string when the result
    should NOT be summarized, None when summarization is allowed.
    """
    threshold_chars = int(getattr(prs, "threshold_chars", 0))
    excluded_tools = set(getattr(prs, "excluded_tools", ()) or ())
    excluded_mats = set(getattr(prs, "excluded_material_types", ()) or ())

    if original_chars < threshold_chars:
        return "below_threshold"
    if tool_name in excluded_tools:
        return "excluded_tool"
    if material_type in excluded_mats:
        return "excluded_material"
    return None


async def run_per_result_fork_agent(
    *,
    raw_text: str,
    tool_name: str,
    deps: AgentRunnerDeps,
) -> str | None:
    """逐结果 LLM 摘要——单次摘要变体。

    摘要器以 ``deps.summarize_fn``（可等待 callable:
    ``async def summarize_fn(prompt: str) -> str``）形式注入。
    缺失依赖或调用失败时回退到字符截断。

    The public name ``run_per_result_fork_agent`` matches the plan.
    """
    summarize_fn = getattr(deps, "summarize_fn", None)
    if summarize_fn is None:
        with contextlib.suppress(Exception):
            emit(_METRIC_PRS_FALLBACK, 1.0, kind="counter", labels={"reason": "no_summarize_fn"})
        return None
    prompt = (
        f"{_load_per_result_prompt()}\n\nTool: {tool_name or '<unknown>'}\n\nResult:\n{raw_text}"
    )
    try:
        summarized = await summarize_fn(prompt)
    except Exception as exc:
        logger.warning("per_result_summary.llm_call_failed err=%s", exc)
        with contextlib.suppress(Exception):
            emit(
                _METRIC_PRS_FALLBACK,
                1.0,
                kind="counter",
                labels={"reason": type(exc).__name__},
            )
        return None
    if not isinstance(summarized, str) or not summarized.strip():
        with contextlib.suppress(Exception):
            emit(
                _METRIC_PRS_FALLBACK,
                1.0,
                kind="counter",
                labels={"reason": "empty_output"},
            )
        return None
    try:
        emit(
            _METRIC_PRS_INVOKED,
            1.0,
            kind="counter",
            labels={"tool_name": tool_name or "unknown"},
        )
        emit(_METRIC_PRS_ORIG, float(len(raw_text)), kind="histogram")
        emit(_METRIC_PRS_COMPACTED, float(len(summarized)), kind="histogram")
    except Exception:
        pass
    return summarized
