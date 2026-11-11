"""Pure-data runtime cache / compression state containers.

Zero prismclaw-internal dependencies — only pydantic. Hoisted from
``engine.services.persistence.run_state.models`` so that
``engine.foundation.telemetry.observability`` can reference them without
creating a foundation → engine.services reverse dependency.
"""

from __future__ import annotations

from pydantic import BaseModel


class PromptCacheRuntimeState(BaseModel):
    prev_cache_read_tokens: int | None = None
    prev_input_tokens: int | None = None
    cache_deletions_pending: bool = False
    pending_post_compaction: bool = False
    cache_editing_header_latched: bool | None = None
    thinking_clear_latched: bool | None = None


class CompressionRuntimeState(BaseModel):
    consecutive_failures: int = 0
    last_successful_summary_text: str = ""
    # SDD R-A2 A-3.5: session-tree entry seq of the last history compaction.
    # -1 = never compacted. Cooldown guard (compaction.history.
    # min_interval_entries) reads this to prevent quasi-continuous
    # compaction when a summary barely reduces the history.
    last_compaction_entry_seq: int = -1
    # v4.0 predictive compaction: recent history_tokens snapshots.
    # Appended each turn after prepare_turn; cleared after successful compaction.
    # Used by predict_compaction_trigger() to estimate when the trigger budget
    # will be reached, allowing proactive compaction before the reactive path.
    recent_history_tokens: list[int] = []
    # 上一轮真实 usage.input_tokens（prompt_tokens），供压缩触发决策做
    # total_input 上界——缺失（首轮 / 后端不回 usage）时回退估算。
    last_prompt_tokens: int | None = None
    # microcompact batch 模式：自上次批量清理以来累计的 R 区工具结果字符。
    # 每轮由调用方累加；批量清理（阈值触发或历史压缩边界）后归零。
    tool_result_chars_since_cleanup: int = 0
    # 上一轮的全量 R 区结果字符快照，用于计算"本轮新增"增量（防止保留段
    # 反复计数导致逐轮触发清理）。
    tool_result_chars_probe: int = 0
