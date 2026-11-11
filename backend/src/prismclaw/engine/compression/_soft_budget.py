from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prismclaw.engine.foundation.metrics import emit


# ──────────────────────────── Soft Budget ────────────────────────────


class SoftBudgetExhausted(Exception):
    """Raised by the runner when the soft-budget terminate threshold is
    crossed mid-turn.

    Caller must catch and stop dispatching new turns; the current
    turn is allowed to finish.
    """


@dataclass(frozen=True)
class SoftBudgetVerdict:
    action: str  # "none" | "warn" | "terminate"
    ratio: float
    effective_window: int
    token_usage: int

    @property
    def should_warn(self) -> bool:
        return self.action in ("warn", "terminate")

    @property
    def should_terminate(self) -> bool:
        return self.action == "terminate"


_METRIC_SOFT_BUDGET_WARN = "soft_budget.warning_injected"
_METRIC_SOFT_BUDGET_TERMINATE = "soft_budget.terminate_triggered"
_METRIC_SOFT_BUDGET_RATIO = "soft_budget.ratio_at_warning"


def evaluate_soft_budget(
    token_usage: int,
    effective_window: int,
    *,
    soft_budget: Any | None,
) -> SoftBudgetVerdict:
    """Decide whether to inject a warning or terminate.

    Mirrors Claude's two-tier pattern:
    - ``token_usage / effective_window`` >= ``warning_ratio`` → warn
    - ``>= terminate_ratio`` → terminate

    Defensive against bad config / zero window: returns ``action="none"``.
    """
    if not soft_budget or effective_window <= 0:
        return SoftBudgetVerdict(
            action="none",
            ratio=0.0,
            effective_window=effective_window,
            token_usage=token_usage,
        )
    warning_ratio = float(getattr(soft_budget, "warning_ratio", 0.9))
    terminate_ratio = float(getattr(soft_budget, "terminate_ratio", 0.95))
    ratio = float(token_usage) / float(effective_window)
    if ratio >= terminate_ratio:
        action = "terminate"
    elif ratio >= warning_ratio:
        action = "warn"
    else:
        action = "none"
    return SoftBudgetVerdict(
        action=action,
        ratio=ratio,
        effective_window=effective_window,
        token_usage=token_usage,
    )


def render_budget_warning(verdict: SoftBudgetVerdict, *, soft_budget: Any | None) -> str:
    """Render the warning template with the current ratio.

    Resolves ``warning_template`` from one of three sources (mirrors
    ``resolve_compact_prompt``):

    1. ``soft_budget.warning_template`` (inline string from config).
    2. The ``prompts/budget_warning.md`` file shipped with the repo.
    3. The hard-coded fallback (last-resort safety net).
    """
    configured = getattr(soft_budget, "warning_template", None) if soft_budget else None
    if configured:
        # Heuristic: multi-line values are always inline.  Single-line
        # values: try to read as a filesystem path; on failure treat
        # as inline.  This keeps backwards-compat with tests / callers
        # that pass short single-line templates.
        if "\n" in configured:
            template = configured
        else:
            candidate = Path(configured)
            if not candidate.is_absolute():
                candidate = (
                    Path(__file__).resolve().parent
                    / "prompts"
                    / configured
                )
            try:
                template = candidate.read_text(encoding="utf-8")
            except OSError:
                template = configured
    else:
        default_path = (
            Path(__file__).resolve().parent
            / "prompts"
            / "budget_warning.md"
        )
        try:
            template = default_path.read_text(encoding="utf-8")
        except OSError:
            template = (
                "<budget_warning ratio=\"{ratio:.0%}\">\n"
                "你已使用有效上下文窗口的 {ratio:.0%}。"
                "请完成当前步骤，返回结果后停止。"
                "不要开始新的探索；除非绝对必要，不要调用更多工具。\n"
                "</budget_warning>"
            )
    try:
        return template.format(ratio=verdict.ratio)
    except Exception:
        # Fall back to a minimal but well-formed warning if the
        # operator-provided template is broken.
        return (
            f"<budget_warning ratio=\"{verdict.ratio:.0%}\">\n"
            f"上下文使用率已达到 {verdict.ratio:.0%}；请完成当前步骤后停止。\n"
            f"</budget_warning>"
        )


def maybe_raise_soft_budget_exhausted(
    verdict: SoftBudgetVerdict,
    *,
    enabled: bool = True,
) -> None:
    """Raise :class:`SoftBudgetExhausted` when the verdict crosses
    the terminate ratio and the kill switch is on.

    Callers that want the runner to keep retrying should pass
    ``enabled=False`` (or set ``CompactionConfig.enable_soft_budget
    = False``).
    """
    if not enabled:
        return
    if verdict.should_terminate:
        try:
            emit(_METRIC_SOFT_BUDGET_TERMINATE, 1.0, kind="counter")
        except Exception:
            pass
        raise SoftBudgetExhausted(verdict)


def inject_budget_warning(
    messages: list[Any],
    verdict: SoftBudgetVerdict,
    *,
    soft_budget: Any | None,
    enabled: bool = True,
) -> list[Any]:
    """Prepend the budget warning to the first system message.

    Mirrors Claude's pattern of "tell the LLM actively".  The
    original message list is not mutated; a new list is returned.

    ``enabled`` is the soft-budget kill switch.  When False, the
    function is a no-op — the caller falls back to the
    pre-refactor passive re-compression path.
    """
    if not enabled or not verdict.should_warn:
        return list(messages)
    warning_text = render_budget_warning(verdict, soft_budget=soft_budget)
    try:
        emit(_METRIC_SOFT_BUDGET_WARN, 1.0, kind="counter")
        emit(_METRIC_SOFT_BUDGET_RATIO, verdict.ratio, kind="histogram")
    except Exception:
        pass
    if verdict.should_terminate:
        try:
            emit(_METRIC_SOFT_BUDGET_TERMINATE, 1.0, kind="counter")
        except Exception:
            pass
    out: list[Any] = []
    injected = False
    for msg in messages:
        if not injected and isinstance(msg, dict) and msg.get("role") == "system":
            content = msg.get("content")
            if isinstance(content, str):
                new_content = f"{content}\n\n{warning_text}"
                new_msg = {**msg, "content": new_content}
                out.append(new_msg)
                injected = True
                continue
        out.append(msg)
    if not injected:
        # No system message to extend — synthesize one at the head.
        out.insert(0, {"role": "system", "content": warning_text})
    return out
