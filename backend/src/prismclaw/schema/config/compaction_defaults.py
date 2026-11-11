from __future__ import annotations

# 本模块常量仅作引擎函数签名的兜底默认（无配置注入路径，如 bootstrap
# 懒加载引擎）。运行期生效值来自 agent.yaml 的 compaction 段：
# history.keep_recent_tokens / predictive_threshold / chunk_token_budget /
# timeout_seconds。
DEFAULT_KEEP_RECENT_TOKENS = 20_000
# 0.75 = predictive compaction triggers at 75% of history_budget, giving
# the system ~5 turns of headroom before reaching the hard budget.
# Combined with the 90% emergency valve in predict_compaction_trigger(),
# the three-tier threshold design is:
#   Predictive (75%): early scheduling, respects cooldown
#   Emergency  (90%): bypasses cooldown to prevent budget overruns
#   Passive   (100%): hard limit fallback
DEFAULT_THRESHOLD = 0.75
DEFAULT_COMPACTION_SAFETY_TIMEOUT_S = 900.0

__all__ = [
    "DEFAULT_COMPACTION_SAFETY_TIMEOUT_S",
    "DEFAULT_KEEP_RECENT_TOKENS",
    "DEFAULT_THRESHOLD",
]
