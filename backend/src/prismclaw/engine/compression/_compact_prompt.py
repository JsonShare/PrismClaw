from __future__ import annotations

import logging
import re

from pathlib import Path
from typing import Any

from prismclaw.engine.foundation.metrics import emit
from prismclaw.schema.config.config import COMPACT_SUMMARY_DEFAULT_TEMPLATE

logger = logging.getLogger(__name__)


def _strip_md_frontmatter(text: str) -> str:
    """Strip HTML comment blocks (<!-- ... -->) and YAML frontmatter (--- ... ---)
    from the head of an md prompt file.  Only strips a single leading block;
    does not touch inline comments.  Returns the remainder verbatim.

    compact_summary.md uses an HTML comment header for human-readable docs;
    we don't want that fed to the LLM as part of the prompt.
    """
    stripped = text.lstrip()
    if stripped.startswith("<!--"):
        end = stripped.find("-->")
        if end != -1:
            stripped = stripped[end + 3:].lstrip()
    elif stripped.startswith("---"):
        # YAML frontmatter
        end = stripped.find("\n---", 3)
        if end == -1:
            end = stripped.find("---\n", 3)
        if end != -1:
            stripped = stripped[end + 4:].lstrip()
    return stripped


# ──────────────────────────── No-Tools Preamble ────────────────────────────


# Leading "do NOT use tools" instruction shipped to the LLM that
# performs the compaction pass.  Sonnet 4.6+ with adaptive
# thinking sometimes still attempts tool calls even after being
# told "return plain text"; per Claude's own telemetry this
# triggers the 2.79% prompt_too_long storm.  Mirrors
# ``NO_TOOLS_PREAMBLE`` in ``prompt.ts:20-30``.
#
# Note: the preamble is intentionally appended to the **user** message,
# not the system message, so it does not perturb the prefix-cache key
# on the system side.
NO_TOOLS_PREAMBLE = (
    "关键：只输出文本，不要调用任何工具。\n"
    "\n"
    "- 不要使用 `read`、`bash`、`grep`、`glob`、`edit`、`write` 或任何其他工具。\n"
    "- 你已经在上述对话中拥有所需的全部上下文。\n"
    "- 工具调用会被拒绝，并浪费你唯一的回合——你将无法完成任务。\n"
    "- 整个回答必须是纯文本：一个 <analysis> 块，后跟一个 <summary> 块。\n"
    "\n"
)

_METRIC_COMPACT_PREAMBLE_INJECTED = "compact.preamble_injected"
_METRIC_COMPACT_TOOL_CALL_REJECTED = "compact.tool_call_rejected_after_preamble"


def inject_no_tools_preamble(
    messages: list[Any],
    *,
    enabled: bool = True,
) -> list[Any]:
    """Prepend ``NO_TOOLS_PREAMBLE`` to the first user message.

    Returns a new list; the input is not mutated.  When ``enabled``
    is False the function is a no-op (used for the ``FLAG_NO_TOOLS_PREAMBLE``
    off path and for tests that need a clean baseline).

    The preamble is appended to the user message rather than the
    system message so the prefix-cache key on the system side
    (which Claude ships as a stable, cacheable block) is not
    perturbed.  See ``prompt.ts:20-30`` for the upstream rationale.
    """
    if not enabled or not messages:
        return list(messages)
    preamble = NO_TOOLS_PREAMBLE
    out: list[Any] = []
    injected = False
    for msg in messages:
        if not injected and isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                new_msg = {**msg, "content": f"{preamble}{content}"}
                out.append(new_msg)
                injected = True
                continue
        out.append(msg)
    if not injected:
        # No user message to extend — synthesize one at the head.
        out.insert(0, {"role": "user", "content": preamble})
    try:
        emit(_METRIC_COMPACT_PREAMBLE_INJECTED, 1.0, kind="counter")
    except Exception:
        pass
    return out


def _resolve_no_tools_preamble_enabled(config: Any) -> bool:
    """Resolve the ``FLAG_NO_TOOLS_PREAMBLE``-equivalent flag from
    the runtime config.  Defaults to on.  Accepts a few attribute
    names so it tolerates older config objects and Pydantic models.
    """
    if config is None:
        return True
    for name in (
        "preamble_no_tools",
        "no_tools_preamble",
        "enable_no_tools_preamble",
    ):
        val = getattr(config, name, None)
        if isinstance(val, bool):
            return val
    # Restructured: preamble_no_tools now lives under the history sub-block.
    history = getattr(config, "history", None)
    if history is not None:
        for name in (
            "preamble_no_tools",
            "no_tools_preamble",
            "enable_no_tools_preamble",
        ):
            val = getattr(history, name, None)
            if isinstance(val, bool):
                return val
    return True


# ──────────────────────────── 9-Section Prompt ────────────────────────────


# Regular expression used by ``strip_analysis_block`` to remove
# the draft <analysis>...</analysis> section from the LLM output.
# The pattern is intentionally case-insensitive (some models
# capitalize the tag) and non-greedy (so the first matching
# close-tag wins).
_ANALYSIS_BLOCK_RE = re.compile(
    r"<\s*analysis\s*>.*?<\s*/\s*analysis\s*>",
    re.IGNORECASE | re.DOTALL,
)

_METRIC_COMPACT_ANALYSIS_STRIPPED = "compact.analysis_stripped"
_METRIC_COMPACT_PROMPT_TOKENS = "compact.prompt_tokens"
_METRIC_COMPACT_OUTPUT_TOKENS = "compact.output_tokens"


def strip_analysis_block(text: str) -> str:
    """Remove the draft ``<analysis>...</analysis>`` block.

    The 9-section prompt asks the LLM to first reason privately in
    an ``<analysis>`` block and then emit the structured
    ``<summary>``.  The analysis is for the model's benefit; we do
    not want to store it (it bloats the summary) or bill tokens for
    it twice.

    Unclosed-tag defense (SDD context-governance W5): a truncated
    generation can emit ``<analysis>`` without the closing tag. A
    dangling opener is never legitimate summary content — everything
    from it to the end of the text is dropped.
    """
    if not text:
        return text
    stripped, n = _ANALYSIS_BLOCK_RE.subn("", text)
    if n > 0:
        try:
            emit(_METRIC_COMPACT_ANALYSIS_STRIPPED, float(n), kind="counter")
        except Exception:
            pass
        # Trim leading/trailing blank lines that the removal left
        # behind so the stored summary is clean.
        return stripped.strip()
    # No closed block — check for a dangling opener (truncated reasoning).
    open_match = re.search(r"<\s*analysis\s*>", stripped, re.IGNORECASE)
    if open_match:
        return stripped[: open_match.start()].strip()
    return text


def _resolve_prompt_template(
    template: Any,
    *,
    default: str,
    default_path: Path,
    config_label: str,
) -> str:
    """Resolve a prompt template from config.

    Accepts three shapes (mirrors the plan §1 / §2 — operators can
    either inline a string, point to a file, or use the upstream
    default):

    1. ``None`` / empty → try ``default_path`` first (single source of
       truth), fall back to ``default`` if the file is missing.
    2. A multi-line literal (contains a newline) → return as-is.
    3. A single-line token → treat as a path.  Relative paths are
       resolved against ``default_path``'s parent for convenience.
       If the file is missing or unreadable, log a warning and fall
       back to ``default``.

    ``config_label`` is used only for diagnostic messages.
    """
    if not template or not isinstance(template, str):
        # default_path 是单一真相源（R4: compact_summary 模板单份化）
        try:
            if default_path.is_file():
                text = default_path.read_text(encoding="utf-8")
                return _strip_md_frontmatter(text)
        except OSError as exc:
            logger.debug("prompt_template.default_path_failed label=%s err=%s", config_label, exc)
        return default
    # Heuristic: multi-line inputs are always inline.  Single-line
    # inputs: try to read as a filesystem path; on failure, treat
    # the value as an inline template.  This is more permissive than
    # "contains a format placeholder" — a single-line marker
    # ("CUSTOM-PROMPT-MARKER") is still a valid inline template.
    if "\n" in template:
        return template
    candidate = Path(template)
    if not candidate.is_absolute():
        candidate = default_path.parent / template
    try:
        return _strip_md_frontmatter(candidate.read_text(encoding="utf-8"))
    except OSError as exc:
        logger.debug(
            "prompt_template.inline_used label=%s value=%s err=%s",
            config_label,
            template,
            exc,
        )
        return template


def resolve_compact_prompt(compaction_config: Any) -> str | None:
    """Resolve the active summarization system prompt.

    Returns the 9-section template when ``cfg.history.prompt.enabled``
    is True (default); returns None when the feature is disabled so
    callers can fall back to ``HARDENED_SUMMARIZER_SYSTEM_PROMPT``.

    Supports three input shapes for ``cfg.history.prompt.template``:

    1. ``None`` / empty → use ``COMPACT_SUMMARY_DEFAULT_TEMPLATE``.
    2. A literal string → use it directly.
    3. A filesystem path (``/abs/path.md`` or ``prompts/x.md``) →
       load the file and use its contents.  Falls back to the
       in-code default if the file is missing or unreadable.
    """
    if compaction_config is None:
        return None
    # 安全默认：history / prompt 缺失时返回 None（调用方走 fallback）。
    # pydantic schema 保证齐全，但 SimpleNamespace 测试桩可能缺字段。
    history = getattr(compaction_config, "history", None)
    if history is None:
        return None
    cfg = getattr(history, "prompt", None)
    if cfg is None:
        return None
    if not bool(cfg.enabled):
        return None
    return _resolve_prompt_template(
        cfg.template,
        default=COMPACT_SUMMARY_DEFAULT_TEMPLATE,
        default_path=Path(__file__).resolve().parent / "prompts" / "compact_summary.md",
        config_label="history.prompt.template",
    )


def resolve_strip_analysis(compaction_config: Any) -> bool:
    """Whether to strip the ``<analysis>`` block before storage.

    Safe default is True (SDD context-governance W5): every consumer of
    this flag stores or injects the summary — a stray reasoning block
    polluting the L4 archive / compaction summary costs far more than
    stripping a block that "shouldn't" be there. Callers that pass no
    config (the L4 archive path builds SummaryEngine without one) get
    stripping; the main history-compaction path always passes an
    explicit config, so its configured value still wins.
    """
    if compaction_config is None:
        return True
    # 安全默认：history / prompt 缺失时返回 True（剥离 analysis 是安全侧）。
    history = getattr(compaction_config, "history", None)
    if history is None:
        return True
    cfg = getattr(history, "prompt", None)
    if cfg is None:
        return True
    return bool(cfg.strip_analysis_block)
