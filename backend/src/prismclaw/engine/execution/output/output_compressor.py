"""Rule-based tool output compressor (OH-1 MVP).

Applies pattern-matched compression rules to tool results before they enter
the LLM message history, reducing token waste from verbose CLI output.

Architecture:
- Rules are matched by tool_name + optional argv pattern + stdout regex
- Matched rules apply a strategy: strip_ansi, collapse_whitespace, head_tail,
  regex_replace, or a chain of these
- Builtin rules cover: git status/diff/log, pytest, npm/pnpm install, pip install
- Compression happens AFTER truncation (truncation is the hard cap; compression
  is semantic reduction within that cap)
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.agent.data_types import ContentBlock, TextBlock
from prismclaw.engine.toolkit.registry.tool_classification import is_search_intent_tool

# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

Strategy = Literal[
    "strip_ansi",
    "collapse_whitespace",
    "head_tail",
    "regex_replace",
]


@dataclass(frozen=True)
class CompressionRule:
    tool_name: str = ""
    argv_pattern: str | None = None
    stdout_pattern: str | None = None
    strategies: tuple[Strategy, ...] = ()
    regex_replacements: tuple[tuple[str, str | Any], ...] = ()
    head_lines: int = 30
    tail_lines: int = 20
    max_chars_before_head_tail: int = 8_000
    # Service-name-decoupled matching: when set it takes precedence over the
    # exact ``tool_name`` comparison (used for configured MCP search tools
    # whose names live in deployment config, not code). ``tool_name`` then
    # serves only as a stats label.
    name_pred: Callable[[str], bool] | None = None

    def matches(self, tool_name: str, argv: str, stdout: str) -> bool:
        if self.name_pred is not None:
            if not self.name_pred(tool_name):
                return False
        elif self.tool_name != tool_name:
            return False
        if self.argv_pattern and not re.search(self.argv_pattern, argv):
            return False
        if self.stdout_pattern and not re.search(self.stdout_pattern, stdout):
            return False
        return True


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_TRAILING_SPACES_RE = re.compile(r"[ \t]+\n")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _collapse_whitespace(text: str) -> str:
    text = _TRAILING_SPACES_RE.sub("\n", text)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text


def _head_tail(text: str, head: int, tail: int, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    lines = text.splitlines(keepends=True)
    if len(lines) <= head + tail + 1:
        return text
    head_part = "".join(lines[:head])
    tail_part = "".join(lines[-tail:])
    omitted = len(lines) - head - tail
    return f"{head_part}\n[... {omitted} lines omitted ...]\n\n{tail_part}"


def _apply_regex_replacements(text: str, replacements: tuple[tuple[str, str], ...]) -> str:
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.MULTILINE)
    return text


_LOSSY_STRATEGIES = ("head_tail", "regex_replace")


def apply_strategies(text: str, rule: CompressionRule) -> str:
    for strategy in rule.strategies:
        text = apply_strategy(text, rule, strategy)
    return text


def apply_strategy(text: str, rule: CompressionRule, strategy: Strategy) -> str:
    """Apply ONE strategy; callers compare before/after to detect lossiness."""
    if strategy == "strip_ansi":
        return _strip_ansi(text)
    if strategy == "collapse_whitespace":
        return _collapse_whitespace(text)
    if strategy == "head_tail":
        return _head_tail(text, rule.head_lines, rule.tail_lines, rule.max_chars_before_head_tail)
    if strategy == "regex_replace":
        return _apply_regex_replacements(text, rule.regex_replacements)
    return text


def is_lossy_strategy(strategy: Strategy) -> bool:
    """Whether a strategy can drop content (vs lossless reformatting).

    SDD T-4: only these justify persisting the pre-compression original.
    """
    return strategy in _LOSSY_STRATEGIES


# ---------------------------------------------------------------------------
# Builtin rules
# ---------------------------------------------------------------------------

BUILTIN_RULES: tuple[CompressionRule, ...] = (
    # git diff — strip ANSI, collapse whitespace, head/tail for large diffs
    CompressionRule(
        tool_name="bash",
        argv_pattern=r"\bgit\s+diff\b",
        strategies=("strip_ansi", "collapse_whitespace", "head_tail"),
        head_lines=50,
        tail_lines=20,
        max_chars_before_head_tail=10_000,
    ),
    # git log — strip ANSI, head/tail for long history
    CompressionRule(
        tool_name="bash",
        argv_pattern=r"\bgit\s+log\b",
        strategies=("strip_ansi", "collapse_whitespace", "head_tail"),
        head_lines=40,
        tail_lines=10,
        max_chars_before_head_tail=8_000,
    ),
    # git status — strip ANSI, collapse whitespace
    CompressionRule(
        tool_name="bash",
        argv_pattern=r"\bgit\s+status\b",
        strategies=("strip_ansi", "collapse_whitespace"),
    ),
    # pytest — strip ANSI, collapse verbose output, head/tail
    CompressionRule(
        tool_name="bash",
        argv_pattern=r"\bpytest\b",
        strategies=("strip_ansi", "regex_replace", "collapse_whitespace", "head_tail"),
        regex_replacements=(
            # Collapse verbose PASSED/FAILED lines to summary
            (r"^(tests/\S+::\S+ )(PASSED|FAILED)\s*\[[ \d]+%\]\s*$", r"\1\2"),
            # Strip coverage tables (long lines of dashes + percentages)
            (r"^-{40,}$", ""),
            # Collapse progress dots
            (r"\.{20,}", lambda m: f"[...{len(m.group())} tests passed...]"),
        ),
        head_lines=30,
        tail_lines=30,
        max_chars_before_head_tail=10_000,
    ),
    # npm/pnpm/yarn install — strip ANSI, collapse added/removed lines
    CompressionRule(
        tool_name="bash",
        argv_pattern=r"\b(npm|pnpm|yarn)\s+install\b",
        strategies=("strip_ansi", "regex_replace", "collapse_whitespace", "head_tail"),
        regex_replacements=(
            (r"^(added|removed|changed) \d+ packages?.*$", ""),
            (r"^npm warn.*$", ""),
        ),
        head_lines=10,
        tail_lines=15,
        max_chars_before_head_tail=5_000,
    ),
    # pip install — strip ANSI, collapse download progress
    CompressionRule(
        tool_name="bash",
        argv_pattern=r"\bpip\s+install\b",
        strategies=("strip_ansi", "regex_replace", "collapse_whitespace", "head_tail"),
        regex_replacements=(
            (r"^\s*Downloading \S+ \([\d.]+ [kMG]B\)$", ""),
            (r"^\s*━+\s+[\d.]+/[\d.]+ [kMG]B.*$", ""),
            (r"^\s*Using cached.*$", ""),
        ),
        head_lines=10,
        tail_lines=10,
        max_chars_before_head_tail=5_000,
    ),
    # docker — strip ANSI, collapse verbose output
    CompressionRule(
        tool_name="bash",
        argv_pattern=r"\bdocker\s+(build|ps|logs|compose)\b",
        strategies=("strip_ansi", "collapse_whitespace", "head_tail"),
        head_lines=30,
        tail_lines=20,
        max_chars_before_head_tail=8_000,
    ),
    # web_fetch — collapse whitespace (HTML → markdown can be verbose)
    CompressionRule(
        tool_name="web_fetch",
        strategies=("collapse_whitespace", "head_tail"),
        head_lines=80,
        tail_lines=20,
        max_chars_before_head_tail=12_000,
    ),
    # Configured web-retrieval search tools — merged JSON payload with
    # cleaned page bodies. Matched via tool_classification (service name
    # lives in config); "tool_name" is a stats label only.
    CompressionRule(
        tool_name="web-retrieval",
        name_pred=is_search_intent_tool,
        strategies=("collapse_whitespace", "head_tail"),
        head_lines=80,
        tail_lines=20,
        max_chars_before_head_tail=12_000,
    ),
)

_BASH_FALLBACK = CompressionRule(
    tool_name="bash",
    strategies=("strip_ansi",),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class CompressionStats:
    original_chars: int = 0
    compressed_chars: int = 0
    rules_matched: list[str] = field(default_factory=list)
    # SDD T-4: True when a lossy strategy (head_tail middle-drop /
    # regex_replace line removal) actually rewrote the text. Callers use
    # this to persist the pre-compression original before the lossy form
    # enters the message history — whitespace/ANSI cleanup is lossless and
    # must NOT trigger persistence.
    lossy: bool = False

    @property
    def saved_chars(self) -> int:
        return self.original_chars - self.compressed_chars

    @property
    def compression_ratio(self) -> float:
        if self.original_chars == 0:
            return 0.0
        return self.saved_chars / self.original_chars


def compress_tool_result(
    result: ToolResult,
    tool_name: str,
    tool_args: dict | str | None = None,
    *,
    rules: tuple[CompressionRule, ...] = BUILTIN_RULES,
    enabled: bool = True,
) -> tuple[ToolResult, CompressionStats]:
    stats = CompressionStats()

    if not enabled:
        return result, stats

    argv = ""
    if isinstance(tool_args, str):
        argv = tool_args
    elif isinstance(tool_args, dict):
        argv = tool_args.get("command", tool_args.get("cmd", ""))

    text_blocks_text = ""
    for block in result.content:
        if isinstance(block, TextBlock):
            text_blocks_text += block.text

    stats.original_chars = len(text_blocks_text)

    if stats.original_chars == 0:
        return result, stats

    matched_rules = [r for r in rules if r.matches(tool_name, argv, text_blocks_text)]
    if not matched_rules:
        if tool_name == "bash" and _BASH_FALLBACK.matches(tool_name, argv, text_blocks_text):
            matched_rules = [_BASH_FALLBACK]
        else:
            stats.compressed_chars = stats.original_chars
            return result, stats

    new_blocks: list[ContentBlock] = []
    for block in result.content:
        if isinstance(block, TextBlock):
            compressed = block.text
            for rule in matched_rules:
                stats.rules_matched.append(
                    f"{rule.tool_name}:{rule.argv_pattern or '*'}"
                )
                for strategy in rule.strategies:
                    before = compressed
                    compressed = apply_strategy(compressed, rule, strategy)
                    if compressed != before and is_lossy_strategy(strategy):
                        stats.lossy = True
            new_blocks.append(TextBlock(text=compressed))
        else:
            new_blocks.append(block)

    compressed_text = ""
    for block in new_blocks:
        if isinstance(block, TextBlock):
            compressed_text += block.text

    stats.compressed_chars = len(compressed_text)

    return (
        ToolResult(
            tool_call_id=result.tool_call_id,
            content=new_blocks,
            is_error=result.is_error,
        ),
        stats,
    )
