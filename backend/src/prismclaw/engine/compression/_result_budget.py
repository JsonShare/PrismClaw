from __future__ import annotations

from typing import Any

from prismclaw.engine.compression.context_zones import (
    clamp_summary_cap,
)


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_ratio(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return parsed


def _resolve_default_max_chars(deps: Any) -> int:
    # per_result_char_cap 是结果裁剪的唯一配置来源（schema 保证非空正整数）；
    # 工具自身的 max_output_chars 属性覆盖仍由 resolve_max_output_chars 处理。
    return deps.config.context.compaction.tool_results.per_result_char_cap


def _resolve_model_for_budget(deps: Any) -> str | None:
    """Resolve the active LLM model id for budget calculations.

    Resolution order:
    1. ``deps.config.context.compaction.model`` (explicit summarizer override)
    2. ``deps.llm.default_model`` (production path)
    3. ``None`` — caller decides what to do.
    """
    override = deps.config.context.compaction.model
    if isinstance(override, str) and override:
        return override
    llm = getattr(deps, "llm", None)
    default_model = getattr(llm, "default_model", None)
    if isinstance(default_model, str) and default_model:
        return default_model
    return None


def _resolve_aggregate_budget_chars(deps: Any, result_count: int) -> int:
    compaction_cfg = deps.config.context.compaction

    explicit = _coerce_positive_int(compaction_cfg.tool_results.aggregate_char_budget)
    if explicit is not None:
        return explicit

    prompt_budget = deps.config.context.budget

    # 优先使用「模型感知」的 effective context window（由 active model +
    # max_output_tokens_for_summary 推导），而非静态 context.budget.window_tokens
    # 默认值。这样避免了大上下文/小输出比（如 gpt-4.1 @ 1M）与小上下文/大输出比
    # （如 gpt-4o @ 128K）模型的预留量错位。
    model = _resolve_model_for_budget(deps)
    fallback_window = int(deps.config.context.budget.window_tokens)
    # 4.4.4：摘要输出预留夹紧到历史预算的 40%（history_budget 取生效值）。
    try:
        _history_budget = int(prompt_budget.compute_history_budget())
    except Exception:
        _history_budget = int(prompt_budget.window_tokens)
    max_output_for_summary = clamp_summary_cap(
        int(compaction_cfg.history.summary_output_token_cap), _history_budget
    )
    if model is None:
        max_context = fallback_window
    else:
        max_context = deps.ports.get_effective_context_window(
            model,
            max_output_tokens_for_summary=max_output_for_summary,
            fallback=fallback_window,
        )

    # Dynamic watermark: compute the real available budget instead of a
    # hardcoded cap. Prefer dynamic_zone_tokens when configured; otherwise
    # remaining = max_context - system_zone - reserve, 30% to tool results.
    dynamic_tokens = _coerce_positive_int(prompt_budget.dynamic_zone_tokens)
    if dynamic_tokens and dynamic_tokens > 0:
        return max(2000, dynamic_tokens * 4)

    system_tokens = _coerce_positive_int(prompt_budget.system_zone_tokens) or 4096
    reserve_tokens = _coerce_positive_int(prompt_budget.output_reserve_tokens) or int(max_context * 0.1)
    remaining_tokens = max(0, max_context - system_tokens - reserve_tokens)

    # Allocate 30% of remaining tokens to tool results
    dynamic_budget = int(remaining_tokens * 0.3)
    return max(4000, dynamic_budget * 4)


def _resolve_context_block_aggregate_budget_chars(
    *,
    token_budget: int | None,
    compaction_config: Any | None,
) -> int:
    if compaction_config is None:
        # 无配置时仅基于 token_budget 推导（ratio 默认 0.6）
        if token_budget is None or token_budget <= 0:
            return 0
        return max(1200, int(token_budget * 4 * 0.6))
    context_blocks = compaction_config.context_blocks
    explicit = _coerce_positive_int(context_blocks.chars)
    if explicit is not None:
        return explicit
    if token_budget is None or token_budget <= 0:
        return 0
    ratio = _coerce_ratio(context_blocks.ratio, default=0.6)
    if ratio <= 0:
        return 0
    budget_chars = token_budget * 4
    # Reserve headroom for runtime/per-turn dynamic blocks so retrieval payloads do
    # not consume the full dynamic budget before later enforcement runs.
    return max(1200, int(budget_chars * ratio))
