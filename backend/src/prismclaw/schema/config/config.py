from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal
try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

# identifier-preservation block.  Mirrors the canonical text
# in ``prismclaw.engine.compression.hardening``.  It is duplicated
# here (rather than imported) because ``compaction.hardening`` is
# loaded transitively by ``compaction.__init__`` → ``compaction.checkpoint``
# → ``prismclaw.kernel``, which forms a cycle when this module
# is being imported.  Keep both copies in sync.
_IDENTIFIER_PRESERVATION_INSTRUCTIONS = (
    "在摘要中逐字保留标识符：UUID、哈希、ID、主机名、IP 地址、端口、URL、"
    "文件名、文件路径、模型名、会话 ID、提交 SHA、错误码。"
    "不得重写、缩写或改写这些标记。"
    "\n\n"
    "用户陈述的事实必须在压缩后继续存在（SDD context-governance W8）："
    "用户就其处境陈述的每项事实都是持久上下文——数字、日期、截止时间、"
    "性能阈值（例如 P99 < 500ms）、团队规模、角色与风格偏好、"
    "过往事件、业务规则以及技术专有名词。每项都要逐字带入摘要的第 10 节。"
    "当输入中包含既有摘要时，其中已列出的每一项用户陈述事实都必须出现在新摘要中——"
    "压缩链一旦首次丢弃某项事实，它就永久丢失。"
    "\n\n"
    "已取代的需求必须作为作废项保留，而不是消失（SDD context-governance W12）："
    "当用户改变主意——更正取值、撤回承诺、取消需求、替换选择——"
    "旧值不是当前上下文，但也不能删除。把它列在第 11 节，每条一行："
    "作废事实 → 替代它的内容（若只是取消，则写“→ 无”）。这是一个禁止清单："
    "其中的任何内容都绝不能再次作为当前事实出现。当输入中包含既有摘要时，"
    "必须逐字继承每一条作废条目——丢失的作废条目会在后续生成中复活"
    "（这正是本节要防止的失效）。某项事实可以出现在第 10 节或第 11 节，"
    "但绝不能同时出现在两处；拿不准时，放在第 11 节并注明不确定性。"
)


class WorkspaceConfig(BaseModel):
    workspaces: dict[str, str] = Field(default_factory=lambda: {"default": "."})

    def resolve_path(self, workspace_id: str) -> Path:
        raw = self.workspaces.get(workspace_id) or self.workspaces.get("default", ".")
        return Path(raw).expanduser().resolve()


class TimeoutConfig(BaseModel):
    run_seconds: float = Field(
        default=300.0,
        description="Outermost timeout protecting a whole agent turn. 0 disables.",
    )
    idle_seconds: float = Field(
        default=180.0,
        description="Max time with no LLM stream progress before aborting. 0 disables.",
    )
    tool_seconds: float = Field(
        default=120.0,
        description="Default per-tool execution cap when tool does not declare its own. 0 disables.",
    )


class RetryConfig(BaseModel):
    reasoning_only_limit: int = Field(
        default=2,
        description="Max retries when assistant returns only reasoning content. 0 disables.",
    )
    empty_response_limit: int = Field(
        default=1,
        description="Max retries when assistant returns empty content. 0 disables.",
    )


# ────────────────────────── Feature flags (kill switches) ──────────────────────────
#
# Each WP has a default-on kill switch so operators can disable a
# behavior change without code changes (and so the "flag off == old
# behavior" verification criterion in the plan is enforceable).
# Names mirror the Claude-side flags referenced in the plan.
FLAG_DYNAMIC_BUDGET_DEFAULT = True
FLAG_AUTOCOMPACT_BREAKER_DEFAULT = True
FLAG_SOFT_BUDGET_DEFAULT = True


class SoftBudgetConfig(BaseModel):
    """Two-tier soft-degrade thresholds for the context window.

    Mirrors the active soft-degrade pattern (90% warn / 95% terminate)
    from Claude's ``compactConversation`` and ``microCompact``: when
    ``token_usage / effective_window`` crosses ``warning_ratio`` the
    coordinator injects a system warning; when it crosses
    ``terminate_ratio`` the current turn is forced to wrap up.
    """

    warning_ratio: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        description="Soft warning threshold. Above this ratio, a budget warning is injected into the next turn.",
    )
    terminate_ratio: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="Hard soft-degrade threshold. Above this ratio, the turn must wrap instead of starting new work.",
    )
    warning_template: str = Field(
        default=(
            "<budget_warning ratio=\"{ratio:.0%}\">\n"
            "你已使用有效上下文窗口的 {ratio:.0%}。"
            "请完成当前步骤，返回结果后停止。"
            "不要开始新的探索；除非绝对必要，不要调用更多工具。\n"
            "</budget_warning>"
        ),
        description="Template rendered into the system message when the warning threshold is crossed.",
    )

    @model_validator(mode="after")
    def _validate_ordering(self) -> Self:
        if self.terminate_ratio < self.warning_ratio:
            raise ValueError(
                f"soft_budget.terminate_ratio ({self.terminate_ratio}) must be "
                f">= warning_ratio ({self.warning_ratio})"
            )
        return self


# 9-section summarization prompt (mirrors Claude's
# ``compactConversation`` template).  Defaults to the upstream
# 9-section template; operators may override ``template`` with a
# domain-specific prompt.  ``strip_analysis_block`` removes the
# draft <analysis>...</analysis> section from the LLM output.
COMPACT_SUMMARY_DEFAULT_TEMPLATE = (
    "# 关键：禁止使用任何工具。只输出文本。\n\n"
    "你的任务是详细总结此前的对话，重点关注用户的明确请求、所做的技术决策，"
    "以及编写或修改过的代码。\n\n"
    "回答必须分成两部分：先在 <analysis> 块中进行私下推理，"
    "然后在 <summary> 块中给出结构化摘要，供另一个 AI 继续执行任务。\n\n"
    "<analysis>\n"
    "1. 按时间顺序逐条分析每条消息。\n"
    "2. 特别留意用户的具体反馈、纠正和意图变化。\n"
    "3. 找出所有文件路径、函数名、错误码和具体取值。\n"
    "</analysis>\n\n"
    "<summary>\n"
    "1. 主要请求与意图\n"
    "2. 关键技术概念\n"
    "3. 文件与代码片段\n"
    "4. 错误与修复\n"
    "5. 问题解决过程\n"
    "6. 用户的所有消息\n"
    "7. 待办任务\n"
    "8. 当前工作\n"
    "9. 可选的下一步\n"
    "10. 用户陈述的事实与约束（逐字保留；必须从既有摘要中完整继承，任何一条都不得丢失）\n"
    "11. 已失效/作废的需求（每条一行：作废事实 → 替代它的内容；必须从既有摘要中完整继承——"
    "一旦丢失作废条目，它会在后续工作中被误当作当前事实复活）\n"
    "</summary>\n\n"
    f"{_IDENTIFIER_PRESERVATION_INSTRUCTIONS}\n"
)


class PerResultSummaryConfig(BaseModel):
    """Per-tool-result LLM summarization.

    When ``enabled=True`` and the result passes the gating checks,
    ``result_compaction._compact_single_result`` asks the
    LLM to summarize a single tool result before handing it to the
    model.  This closes the gray zone where a single huge result
    is character-truncated mid-session and breaks the story line.

    Defaults to off — careful rollout, per the plan.  Operators
    that want this on must also wire an LLM call into
    ``deps.summarize_fn`` (the pipeline calls
    ``await deps.summarize_fn(text)`` when needed).
    """

    enabled: bool = Field(
        default=False,
        description=(
            "When False (default), per-result LLM summarization is "
            "skipped and the legacy character-truncation path is used. "
            "Set to True to opt in."
        ),
    )
    threshold_chars: int = Field(
        default=8_000,
        ge=1,
        description="Only invoke the per-result summarizer when the raw result is at least this many characters.",
    )
    excluded_tools: tuple[str, ...] = Field(
        default=("Bash",),
        description=(
            "Tool names that must NEVER be summarized per-result. "
            "Defaults to 'Bash' because command output is rarely "
            "improved by an LLM pass (and the diff is what matters)."
        ),
    )
    excluded_material_types: tuple[str, ...] = Field(
        default=("command_output",),
        description=(
            "Result material types that must NEVER be summarized "
            "per-result.  Mirrors the plan: bash output has low "
            "summary value."
        ),
    )
    target_chars: int = Field(
        default=2_000,
        ge=1,
        description=(
            "Target size of the LLM-generated summary.  The "
            "summarizer is asked to fit its output within this many "
            "characters."
        ),
    )


class CompactPromptConfig(BaseModel):
    """9-section summarization prompt configuration.

    Mirrors Claude's ``compactConversation`` 9-section template
    (Primary Request, Key Technical Concepts, Files, Errors, Problem
    Solving, User Messages, Pending Tasks, Current Work, Optional
    Next Step).  Operators can replace ``template`` with a
    domain-specific prompt; ``strip_analysis_block`` controls whether
    the draft <analysis> section is removed from the LLM output.
    """

    template: str | None = Field(
        default=None,
        description=(
            "9-section summarization prompt. Override to use a domain-specific prompt. "
            "None uses the built-in default (COMPACT_SUMMARY_DEFAULT_TEMPLATE)."
        ),
    )
    strip_analysis_block: bool = Field(
        default=True,
        description="If True, the <analysis>...</analysis> block is removed from the LLM output before storage.",
    )
    enabled: bool = Field(
        default=True,
        description=(
            "Feature flag. When False, the legacy short summarization "
            "prompt is used instead of the 9-section default."
        ),
    )


# Note: this constant is defined AFTER the IDENTIFIER_PRESERVATION_INSTRUCTIONS
# import above.  Pydantic reads default values from this constant
# when constructing ``CompactPromptConfig`` instances.


# ─────────────────────────── Compaction sub-blocks ───────────────────────────
#
# The compaction block owns four distinct responsibilities.  Each is isolated
# in its own sub-model so the field names make the responsibility obvious and an
# operator can tune one concern without affecting the others:
#
#   * ``history``       — whole-conversation LLM summarization (the "context
#                         compaction" pass that shrinks the message history).
#   * ``tool_results``  — per-result + aggregate trimming of tool output within
#                         a single turn, including disk persistence of
#                         oversized results.
#   * ``context_blocks`` — budget for retrieval / document context blocks before
#                         dynamic-budget trimming.
#   * ``safety``        — circuit breaker, soft budget, and dynamic-budget
#                         governance kill-switches.


class HistoryCompactionConfig(BaseModel):
    """Whole-conversation history compaction (LLM summary of the thread)."""

    model_config = {"populate_by_name": True}

    keep_recent_tokens: int = Field(
        default=20_000,
        description="Minimum tokens of recent messages preserved uncompacted.",
    )
    min_interval_entries: int = Field(
        default=25,
        ge=0,
        description=(
            "Cooldown between two history compactions in the same session, "
            "measured in session-tree entry count (~5 user turns; one turn "
            "produces 3-6 entries). SDD R-A2 A-3.5: guards against "
            "quasi-continuous compaction when a compaction succeeds but barely "
            "reduces (huge summaries / keep_recent-heavy history). 0 disables."
        ),
    )
    summary_output_token_cap: int = Field(
        default=20_000,
        ge=1,
        description="Cap on the output reservation subtracted from the model's advertised context window when computing the effective budget.",
    )
    prompt: "CompactPromptConfig" = Field(
        default_factory=lambda: CompactPromptConfig(),
        description="9-section summarization prompt configuration. Mirrors Claude's compactConversation template.",
    )
    preamble_no_tools: bool = Field(
        default=True,
        description="Prepend the no-tools preamble to summarizer user messages. Mirrors Claude's FLAG_NO_TOOLS_PREAMBLE.",
    )
    predictive_horizon: int = Field(
        default=3,
        ge=1,
        description="Predictive compaction: how many turns ahead to project. "
                    "When recent history growth suggests the trigger budget will be "
                    "reached within this horizon, compaction is initiated proactively.",
    )
    predictive_min_samples: int = Field(
        default=4,
        ge=2,
        description="Predictive compaction: minimum history_tokens snapshots required "
                    "before prediction is attempted. Prevents premature triggers from "
                    "insufficient data.",
    )


class ToolResultCompactionConfig(BaseModel):
    """Per-result + aggregate tool-result trimming within a single turn."""

    model_config = {"populate_by_name": True}

    per_result_char_cap: int = Field(
        default=8000,
        ge=1,
        description="Per-result inline char cap. Single source of truth for result truncation; "
                    "per-tool max_output_chars attribute still overrides when present.",
    )
    aggregate_char_budget: int | None = Field(
        default=None,
        description="Explicit aggregate char budget for all tool results in one turn. None derives from context.budget.dynamic_zone_tokens.",
    )
    per_result_summary: "PerResultSummaryConfig" = Field(
        default_factory=lambda: PerResultSummaryConfig(),
        description="Per-result LLM summarization thresholds. Closes the per-result character-truncation gray zone.",
    )


class ContextBlockBudgetConfig(BaseModel):
    """Retrieval / document context-block budget before dynamic trimming."""

    model_config = {"populate_by_name": True}

    chars: int | None = Field(
        default=None,
        description="Explicit aggregate char budget for context blocks. None derives from the current token budget.",
    )
    ratio: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Share of dynamic token budget reserved for context blocks when no explicit chars budget is provided.",
    )


class CompactionSafetyConfig(BaseModel):
    """Governance: circuit breaker, soft budget, dynamic budget kill-switches."""

    model_config = {"populate_by_name": True}

    max_consecutive_failures: int = Field(
        default=3,
        ge=1,
        description="Consecutive autocompact failures before the breaker opens. Mirrors Claude's MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES.",
    )
    enable_autocompact_breaker: bool = Field(
        default=FLAG_AUTOCOMPACT_BREAKER_DEFAULT,
        description="Kill switch. Default on. When False, do not enforce the consecutive-failure breaker; compaction is retried indefinitely.",
    )
    soft_budget: "SoftBudgetConfig" = Field(
        default_factory=lambda: SoftBudgetConfig(),
        description="Soft budget thresholds. Mirrors Claude's 90%/95% active soft-degrade pattern.",
    )
    enable_soft_budget: bool = Field(
        default=FLAG_SOFT_BUDGET_DEFAULT,
        description="Kill switch. Default on. When False, do not inject soft-budget warnings or escalate at the terminate ratio.",
    )
    enable_dynamic_budget: bool = Field(
        default=FLAG_DYNAMIC_BUDGET_DEFAULT,
        description="Kill switch. Default on. When False, use legacy static window budget (context.budget.window_tokens * 0.8 chars) regardless of the active model.",
    )


class CompactionConfig(BaseModel):
    """Top-level compaction configuration.

    Four responsibilities, each owned by a dedicated sub-block so the field
    names make the responsibility obvious:

      * ``history``      — whole-conversation LLM summarization.
      * ``tool_results`` — per-result + aggregate tool-output trimming (incl. disk persistence).
      * ``context_blocks`` — retrieval / document context-block budget.
      * ``safety``       — circuit breaker, soft budget, dynamic budget governance.
    """

    model: str | None = Field(
        default=None,
        description="Override LLM model for summarization. If None, the chat model is used.",
    )
    timeout_seconds: float = Field(
        default=900.0,
        description="Safety timeout for a compaction attempt (mirrors timeouts.compaction_seconds).",
    )
    history: "HistoryCompactionConfig" = Field(
        default_factory=lambda: HistoryCompactionConfig(),
        description="Whole-conversation history compaction settings.",
    )
    tool_results: "ToolResultCompactionConfig" = Field(
        default_factory=lambda: ToolResultCompactionConfig(),
        description="Per-result + aggregate tool-result trimming (incl. disk persistence).",
    )
    context_blocks: "ContextBlockBudgetConfig" = Field(
        default_factory=lambda: ContextBlockBudgetConfig(),
        description="Retrieval / document context-block budget.",
    )
    safety: "CompactionSafetyConfig" = Field(
        default_factory=lambda: CompactionSafetyConfig(),
        description="Circuit breaker, soft budget, and dynamic-budget governance.",
    )
    ptl_min_keep_tokens: int = Field(
        6400,
        ge=0,
        description="PTL 截断重试后最小保留 token 数（128K 缩放）。",
    )
    reinject_max_files: int = Field(
        5,
        ge=1,
        description="压缩后文件再注入的最大文件数。",
    )
    predictive_threshold: float = Field(
        0.75,
        ge=0.0,
        le=1.0,
        description="预测性压缩触发比例（占 history_budget）；90% 紧急阀与 100% 硬上限见 predict_compaction_trigger。",
    )
    chunk_token_budget: int = Field(
        8000,
        ge=1,
        description="分块摘要的单块 token 预算。",
    )
    microcompact_keep_recent: dict[str, int] = Field(
        default_factory=lambda: {
            # 与 engine/context/microcompact.py TOOL_KEEP_RECENT 分档表等价
            "web_search": 1, "web_fetch": 1, "web_extract": 1,
            "fetch_mcp_resource": 1,
            "tool_search": 1, "skill_search": 1, "activate_skill": 1,
            "bash": 3, "bash_read": 3, "python": 3,
            "ls": 4, "grep": 4, "glob": 4,
            "reduce_subagent_results": 3,
            "read": 8, "read_image": 6, "read_office": 6,
        },
        description="microcompact 分工具保留窗口（tiered retention）。",
    )
    microcompact_mode: Literal["incremental", "batch"] = Field(
        "batch",
        description=(
            "microcompact 触发模式：incremental 每轮滚动改写工具结果；"
            "batch 仅在历史压缩边界或 R 区累计字符超预算时批量清理（v4 §5.4）。"
        ),
    )

    model_config = {"populate_by_name": True}


class CompletionControlConfig(BaseModel):
    """完成控制内核的修复额度。

    轮次 / 墙钟 / 金额上限不在这里：它们仍归 ``max_iterations`` /
    ``timeouts.run_seconds`` / ``max_budget_usd``，由 BudgetGuard 统一读取。
    修复额度属于「完成要求」，因此挂在契约上、由本配置给出上限。
    """

    max_repairs: int = Field(
        default=1,
        ge=0,
        description=(
            "完成契约的修复额度：模型停止但准则未满足时，最多注入几次"
            "修复驱动。用尽后判定 blocked_no_progress 并终止。"
        ),
    )


class RunLoopTuningConfig(BaseModel):
    tool_max_uses: dict[str, int] = Field(
        default_factory=lambda: {"web-search-mcp__search": 6},
        description=(
            "Per-run max uses per tool name. Keys are LLM-facing names; MCP "
            "tools use the '{server}__{tool}' rewrite (e.g. 'web-search-mcp__search')."
        ),
    )
    search_intent_tools: list[str] = Field(
        default_factory=lambda: ["web-search-mcp__search"],
        description="Tool names treated as search/fetch intent for loop detection.",
    )
    search_loop_warn_at: int = Field(
        default=5,
        ge=1,
        description="Cumulative search-intent tool call count that triggers a Warn guidance injection. "
                    "Works with search_freeze_output_share as a dual-condition Freeze gate.",
    )
    search_freeze_output_share: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Tool-output-char share of history that, combined with search_loop_warn_at, "
                    "triggers Freeze (removing search tools from the visible list). "
                    "A share >= this value after a Warn indicates search is consuming the budget.",
    )
    same_target_read_warn_at: int = Field(
        default=4,
        ge=1,
        description="Cumulative reads of one target (file/URL/query) with no write to it "
                    "before Check 5 emits a same-target re-read advisory. Advisory-only: "
                    "reads are essential to the task, so unlike search tools they are never frozen.",
    )
    error_fatigue_threshold: int = Field(
        default=3,
        description="Consecutive INTERNAL tool errors (parameter/usage bugs; external data-source "
                    "failures are attributed separately and never counted here) that trigger a soft "
                    "warn guidance (model may be fixating on a broken approach). It is diagnostic "
                    "input, NOT a termination signal — run termination belongs solely to "
                    "CompletionKernel (budget / completion contract / recovery fuse). 0 disables.",
    )
    repeated_tool_signature_threshold: int = Field(
        default=3,
        description="Repeated identical tool-call signature threshold that also triggers stall finalization when progress is low. 0 disables.",
    )
    doom_loop_threshold: int = Field(
        default=3,
        ge=1,
        description="Hard cap on consecutive identical tool-call signatures (aligned with opencode's "
                    "DOOM_LOOP_THRESHOLD=3). Escalation ladder in Check 2 of call_handling.py: at "
                    "repeated_tool_signature_threshold the run gets a soft warn; ABOVE this cap it gets a "
                    "structured corrective message injected into pending_messages (the loop still continues); "
                    "at 2x this cap the run FREEZES that call signature — further identical calls are blocked "
                    "before execution. Re-attempting a frozen call up to this cap sets the frozen_call_defied "
                    "suspicion flag; the run-level terminate decision belongs solely to CompletionKernel. "
                    "The 2x absolute max is clamped to the 12-entry recent_tool_signatures window, "
                    "so values above 6 make the freeze stage unreachable and leave only the "
                    "corrective-injection stage. The same ladder thresholds also govern the bounded-cycling "
                    "detection (Check 2b): a full signature window confined to <=2 distinct call+result "
                    "combinations warns at this threshold and raises the cycling suspicion flag at 2x "
                    "(the loop is still never terminated locally).",
    )

    # run 级终态唯一由 CompletionKernel 裁决（预算 / 完成契约 / 恢复熔断）。
    # 工具循环阶梯（doom-loop / 冻结 / 重读提醒）只产引导，不终止。
    # arbiter_recovery_chain_limit 是 RecoveryPolicy 的恢复熔断上限。
    arbiter_recovery_chain_limit: int = Field(
        default=3,
        description="Max consecutive recovery turns before fatal.",
    )


class SubcapsSettings(BaseModel):
    """子额度（sub-caps）：上下文分区的细粒度预算（批 3 外置）。

    原为分散在各引擎模块级的魔法常量，现统一收敛到此。``subagents``
    由 3000 → 1500 属计划内变更（SDD D11 渐进披露），其余字段为等价迁移。
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    subagents: int = Field(1500, ge=0)
    skills_ratio: float = Field(0.01, ge=0.0, le=1.0)
    listing_desc_chars: int = Field(250, ge=1)
    runtime_status: int = Field(500, ge=1)
    l1_snapshot: int = Field(500, ge=1)
    skill_attachment: int = Field(6000, ge=1)
    doc_inline_min_chars: int = Field(2500, ge=1)
    doc_inline_max_chars: int = Field(7500, ge=1)
    max_constraints: int = Field(4, ge=0)
    # ratio 模式附件 token 预算：min(ratio·input_budget, cap)；字符按 ×1.5 缩放
    attachment_token_ratio: float = Field(0.25, ge=0.0, le=1.0)
    attachment_token_cap: int = Field(24000, ge=0)
    # 压缩后文件重注入：单文件 token 预算（全局 = 本值 × compaction.reinject_max_files，不另设键）
    reinject_file_tokens: int = Field(3200, ge=0)
    # 技能 preload 注入 token 上限
    skill_preload: int = Field(7500, ge=0)


class BudgetZoneSpec(BaseModel):
    """比例模式（mode=ratio）下单个分区的配比规格。

    ratio 为该分区占可用输入窗口 A_eff 的比例；floor 为分区预算下限
    （token）；cap 为分区预算上限（token）。
    """

    model_config = {"populate_by_name": True}

    ratio: float = Field(
        default=0.12,
        ge=0.0,
        le=1.0,
        description="该分区占可用输入窗口（A_eff）的比例。",
    )
    floor: int = Field(
        default=2048,
        ge=0,
        description="该分区预算下限（token）。",
    )
    cap: int = Field(
        default=12288,
        ge=0,
        description="该分区预算上限（token）。",
    )

    @model_validator(mode="after")
    def _floor_le_cap(self) -> "BudgetZoneSpec":
        # floor > cap 会让 clamp 上下限倒挂（floor 保障被静默破坏），
        # 配置错误在加载期响亮拒绝。
        if self.floor > self.cap:
            raise ValueError(
                f"zone floor ({self.floor}) must be <= cap ({self.cap})"
            )
        return self


class OutputZoneSpec(BaseModel):
    """比例模式下输出预留（output）的规格。

    max 为 O 的硬上限（O ≤ min(round(0.3·W), max)，v4 §3.2）；ratio 字段
    本批暂不参与 O 公式，保留给后续阶段使用。
    """

    model_config = {"populate_by_name": True}

    ratio: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="输出预留占窗口的比例（本批暂不参与求解，保留供后续阶段）。",
    )
    max: int = Field(
        default=65536,
        ge=0,
        description="输出预留硬上限（token）。O 无论如何不超过 min(round(0.3·W), max)。",
    )


class ContextZonesConfig(BaseModel):
    """比例模式各分区（system/dynamic/result/tools）与输出预留的配比规格。

    字段名固定为 zones.{system,dynamic,result,tools,output}，对应
    agent.yaml 的 ``context.budget.zones.*``。tools.cap 为占位（真实上限
    来自实测 wire，即 ``ToolsRegistry.wire_tokens_estimate()``）。
    """

    model_config = {"populate_by_name": True}

    system: BudgetZoneSpec = Field(
        default_factory=lambda: BudgetZoneSpec(ratio=0.12, floor=2048, cap=12288),
        description="冻结区（system 前缀）配比。",
    )
    dynamic: BudgetZoneSpec = Field(
        default_factory=lambda: BudgetZoneSpec(ratio=0.15, floor=2048, cap=16384),
        description="动态区（dynamic 尾部 <system-context>）配比。",
    )
    result: BudgetZoneSpec = Field(
        default_factory=lambda: BudgetZoneSpec(ratio=0.15, floor=2048, cap=16384),
        description="结果区（result，工具结果消息）配比。",
    )
    tools: BudgetZoneSpec = Field(
        default_factory=lambda: BudgetZoneSpec(ratio=0.18, floor=1024, cap=65536),
        description=(
            "工具 schema wire 预算配比。cap 用 65536 占位——T 由 clamp 实测 wire "
            "到 floor 与 ratio·A_eff 之间，真实上限来自实测 wire。"
        ),
    )
    output: OutputZoneSpec = Field(
        default_factory=OutputZoneSpec,
        description="输出预留（output）规格。",
    )


class ContextBudgetConfig(BaseModel):
    """上下文窗口与分区预算。

    收敛了原先分散在 ``agent.max_context_tokens`` 与 ``agent.promptBudget``
    两处的配置。history 可用空间 = 窗口 - 冻结区 - 动态区 - 输出预留。
    """

    model_config = {"populate_by_name": True}

    mode: Literal["absolute", "ratio"] = Field(
        default="absolute",
        description=(
            "预算求解模式：absolute 走固定四键（window/system/dynamic/output_reserve，"
            "回滚基石）；ratio 走 zones 比例求解（v4 §3.2）。"
        ),
    )
    zones: ContextZonesConfig = Field(
        default_factory=ContextZonesConfig,
        description="比例模式（mode=ratio）下各分区与输出预留的配比规格。",
    )

    window_tokens: int = Field(
        default=131_072,
        description="Total context window (128K = 131072). Sizing baseline for all budgets.",
    )
    system_zone_tokens: int = Field(
        default=4096,
        description="Hard cap for frozen zone (identity + tools + skills_index + workspace).",
    )
    dynamic_zone_tokens: int = Field(
        default=4096,
        description="Budget for user message injection area (working_memory, memory_context, etc.).",
    )
    output_reserve_tokens: int | None = Field(
        default=16_384,
        description="Tokens reserved for LLM output. Covers model max_output; "
                    "None falls back to the active model's max_output_tokens.",
    )
    subcaps: SubcapsSettings = Field(default_factory=SubcapsSettings)
    # History-budget floor (SDD W15) — 原为模块级常量，现外置为配置字段。
    history_floor_min: int = Field(4096, ge=0)
    history_floor_divisor: int = Field(4, ge=1)

    @model_validator(mode="after")
    def _validate_zones(self) -> "ContextBudgetConfig":
        """固定分区之和不得超过窗口（分区溢出与显式预留溢出一并校验）。

        仅对 absolute 模式生效；ratio 模式的分区由 resolve_context_zones
        在运行时 clamp，不在此处做固定值校验。
        """
        if self.mode != "absolute":
            return self
        zones = self.system_zone_tokens + self.dynamic_zone_tokens
        if zones > self.window_tokens:
            raise ValueError(
                f"context.budget zones ({zones}) exceed window_tokens ({self.window_tokens}): "
                f"system_zone={self.system_zone_tokens} + dynamic_zone={self.dynamic_zone_tokens} = {zones}"
            )
        if self.output_reserve_tokens is not None and zones + self.output_reserve_tokens > self.window_tokens:
            total = zones + self.output_reserve_tokens
            raise ValueError(
                f"context.budget zones ({total}) exceed window_tokens ({self.window_tokens}): "
                f"system_zone={self.system_zone_tokens} + dynamic_zone={self.dynamic_zone_tokens} "
                f"+ output_reserve={self.output_reserve_tokens} = {total}"
            )
        return self

    def compute_history_budget(
        self,
        model_max_output: int | None = None,
        *,
        tools_wire_tokens: int = 0,
    ) -> int:
        """计算对话历史可用 token 预算。

        预算 = 窗口 − 固定分区 − 工具 wire 成本 − 输出预留，且永不低于
        下限（剩余空间的 1/4，至少 4096）。

        工具 wire 成本（SDD R-A2 / W13，A-0 实测 E14）：每个请求随
        litellm ``tools`` 参数发送的工具 schema JSON 是真实的窗口占用，
        此前完全未入账——budget_remaining 对运维是误导指标。值来自
        ``ToolRegistry.wire_tokens_estimate()``（指纹缓存 + 序列化字节 ÷ 5
        标定，31 工具 ≈6.4k tokens；旧 CJK 启发式给 11.8k，高估 1.8×），
        全量注册表测量是 governance 过滤子集的保守上界。

        下限防御（SDD W15）：旧实现在预留吃掉全部剩余空间时返回 0，
        ``assess_history_compaction`` 的 ``history_budget > 0`` 守卫随即
        **静默禁用压缩**（小窗口 + 大 max_output 的配置必踩——实测 32k
        窗口 0 压缩、64k 真实 input 全程放行）。下限保证压缩始终可用，
        只是更激进；命中下限说明配置失衡，每轮打 ERROR 暴露问题，
        绝不静默。
        """
        if self.mode == "ratio":
            from prismclaw.engine.compression.context_zones import (
                resolve_context_zones,
            )

            return resolve_context_zones(
                window=self.window_tokens,
                model_max_output=model_max_output,
                budget=self,
                tools_wire=tools_wire_tokens,
                image_tokens=0,
            ).history

        remaining = (
            self.window_tokens
            - self.system_zone_tokens
            - self.dynamic_zone_tokens
            - max(0, tools_wire_tokens)
        )
        reserve = self._resolve_output_reserve(remaining, model_max_output)
        # Clamp reserve: output reserve cannot exceed remaining after zones+wire
        # (e.g. output_reserve_tokens=90000 with remaining=103916 would leave
        # only 13916 for history — well below the floor).  This makes the
        # budget self-correcting without requiring operator config changes.
        reserve = min(reserve, max(0, remaining - self.history_floor_min))
        floor = max(self.history_floor_min, remaining // self.history_floor_divisor)
        uncapped = remaining - reserve
        if uncapped < floor:
            logger.warning(
                "context.budget: history budget hit the floor (%d) — reserve %d "
                "leaves only %d of %d remaining tokens (window=%d zones=%d+%d "
                "tools_wire=%d). Compaction stays enabled but is more aggressive "
                "than configured. Rebalance window_tokens/zones/reserve or shrink "
                "the tool registry.",
                floor, reserve, uncapped, remaining,
                self.window_tokens, self.system_zone_tokens,
                self.dynamic_zone_tokens, tools_wire_tokens,
            )
            return floor
        return uncapped

    def _resolve_output_reserve(self, remaining: int, model_max_output: int | None) -> int:
        # 预留来源：显式配置 > 模型 max_output > 0（不预留，输出空间由
        # 请求层的 max_tokens 钳制兜底）——旧的 ratio 兜底在此处已删除。
        reserve = self.output_reserve_tokens
        if reserve is None:
            reserve = model_max_output or 0
        return min(reserve, remaining)


class ContextConfig(BaseModel):
    """上下文组装与压缩总配置（agent.context 块）。

    将原先分散在 agent.max_context_tokens / agent.promptBudget /
    agent.compaction 三处的上下文相关配置收敛为单一块：budget（窗口与
    分区）、compaction（历史压缩与治理）。
    """

    model_config = {"populate_by_name": True}

    budget: ContextBudgetConfig = Field(default_factory=ContextBudgetConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)


class VisionConfig(BaseModel):
    model_config = {"populate_by_name": True}

    model: str = "Qwen3-VL-30B-A3B-Instruct"
    api_key: str = ""
    base_url: str = "http://211.157.155.210:8006/v1"
    timeout_seconds: int = Field(90, gt=0)
    # 单张内联图片的 token 估算（进入 ratio A_eff 的 image_tokens 扣除项）。
    tokens_per_image: int = Field(1024, ge=0)
    # 单轮可内联进主模型的图片上限；超过的画片按现状落盘逻辑不变。
    max_images_inline: int = Field(4, ge=0)


class CuratorConfig(BaseModel):
    """Curator lifecycle management config."""
    model_config = {"populate_by_name": True}
    enabled: bool = True
    check_interval_seconds: int = Field(3600)
    interval_seconds: int = Field(604800)
    stale_after_days: int = Field(30)
    archive_after_days: int = Field(90)
    graduation_enabled: bool = Field(True)
    graduation_mode: str = Field("template")
    graduation_model: str | None = Field(None)
    promotion_min_use_count: int = Field(5)
    promotion_min_days: int = Field(7)
    llm_review_enabled: bool = Field(False)
    llm_review_model: str | None = Field(None)
    llm_review_interval_seconds: int = Field(1209600)
    llm_review_actions: list[str] = Field(default_factory=lambda: ["promote"])
    llm_review_max_batch: int = Field(20)


class DreamConfig(BaseModel):
    """Dream consolidation config."""
    model_config = {"populate_by_name": True}
    enabled: bool = True
    idle_check_interval_seconds: int = Field(300)
    idle_threshold_seconds: int = Field(1800)
    nightly_check_interval_seconds: int = Field(1800)
    nightly_timezone: str = Field("local")
    nightly_start_hour_local: int = Field(1)
    nightly_end_hour_local: int = Field(5)
    nightly_catch_up_grace_minutes: int = Field(90)
    nightly_max_sessions: int = Field(10)
    multi_worker_scan_policy: str = Field("leader_only")
    publish_channel: str | None = Field(None)
    memory_publish_channel: str | None = Field(None)
    artifact_publish_channel: str | None = Field(None)
    canary_rollout_percentage: int = Field(0)
    canary_rollout_salt: str = Field("dream-canary")
    shadow_publish_enabled: bool = Field(False)
    shadow_memory_publish_enabled: bool | None = Field(None)
    shadow_artifact_publish_enabled: bool | None = Field(None)
    tiers_enabled: bool = Field(False)
    contest_exit_days: int = Field(30)
    deep_merge_min_positive: int = Field(1)
    correction_learning_enabled: bool = Field(False)


class AffinityConfig(BaseModel):
    """Session affinity config."""
    model_config = {"populate_by_name": True}
    enabled: bool = False
    ttl_seconds: int = 300
    heartbeat_interval: int = 30
    stale_threshold: int = 90
    forward_prefix: str = "prismclaw:forward:"
    renewal_interval: int = 60


class EvolutionConfig(BaseModel):
    model_config = {"populate_by_name": True}

    enabled: bool = True
    extraction_model: str | None = None
    max_candidates: int = 100
    min_tool_calls_for_extraction: int = Field(2)
    dedup_overlap_threshold: float = 0.6
    max_sops_per_extraction: int = 5
    description_max_chars: int = 150
    procedure_max_chars: int = 5000
    curator_enabled: bool = True
    dream_enabled: bool = True
    curator: CuratorConfig = Field(default_factory=CuratorConfig)
    dream: DreamConfig = Field(default_factory=DreamConfig)


class SubAgentConfig(BaseModel):
    model_config = {"populate_by_name": True}

    enabled: bool = True
    max_depth: int = 1
    max_subagent_depth: int = 3
    # 静态发现链（repo/assets 管控，绝对 home 路径，与 SubAgentSettings.asset_subagents_dir 对齐）
    asset_subagents_dir: str = "~/.prismclaw/assets/agents"
    default_max_turns: int = 30
    default_timeout_seconds: float = 300.0
    max_parallel_per_turn: int = Field(5, ge=1, le=20)
    handoff_enabled: bool = True
    max_handoff_chain_length: int = Field(5, ge=1, le=20)
    pending_handoff_ttl_turns: int = Field(3, ge=1, le=10)
    handoff_reverse_lookup_enabled: bool = True


class SandboxFilesystemRule(BaseModel):
    """Sandbox default filesystem access rules."""
    model_config = {"populate_by_name": True}
    allow_write: list[str] = Field(default_factory=list)
    deny_read: list[str] = Field(default_factory=list)
    deny_write: list[str] = Field(default_factory=list)


class SandboxNetworkRule(BaseModel):
    """Sandbox default network access rules."""
    model_config = {"populate_by_name": True}
    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(
        default_factory=lambda: ["169.254.169.254"],
    )


class SandboxConfig(BaseModel):
    model_config = {"populate_by_name": True}
    enforce: bool = True
    default_filesystem: SandboxFilesystemRule = Field(default_factory=SandboxFilesystemRule)
    default_network: SandboxNetworkRule = Field(default_factory=SandboxNetworkRule)
    default_env_allowlist: list[str] = Field(default_factory=list)


class ProviderModelSpecEntry(BaseModel):
    model_config = {"populate_by_name": True}

    context_window: int | None = None
    max_output_tokens: int | None = None


class ProviderModelSpecs(BaseModel):
    model_config = {"populate_by_name": True}

    providers: dict[str, dict[str, ProviderModelSpecEntry]] = Field(default_factory=dict)


class BudgetPricingConfig(BaseModel):
    """成本天花板（loop 防护第 3 层）的 USD 折算费率。

    单位：USD / million tokens。默认值按中档模型保守估计
    （略高估 → 天花板偏早触发，安全方向）。当前单模型部署
    （glm-53）用统一费率即可；多模型路由落地后再按模型细分。
    """

    input_per_mtok: float = Field(default=1.0, ge=0.0)
    output_per_mtok: float = Field(default=3.0, ge=0.0)
    cache_read_per_mtok: float = Field(default=0.1, ge=0.0)
    cache_write_per_mtok: float = Field(default=1.25, ge=0.0)

    def cost_for_usage(self, usage: Any) -> float:
        """LLMUsage → USD。字段缺失/None 按 0 计（getattr 容错）。

        语义假设 input_tokens 不含 cache 部分（Anthropic 风格的
        独立字段）；若适配层重复计入，成本被高估——对天花板是
        安全方向。
        """
        if usage is None:
            return 0.0
        input_t = int(getattr(usage, "input_tokens", 0) or 0)
        output_t = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read_t = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_write_t = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        return (
            input_t / 1e6 * self.input_per_mtok
            + output_t / 1e6 * self.output_per_mtok
            + cache_read_t / 1e6 * self.cache_read_per_mtok
            + cache_write_t / 1e6 * self.cache_write_per_mtok
        )


class AgentRunConfig(BaseModel):
    max_iterations: int = Field(
        default=70,
        description="Hard iteration cap per run. 0 disables the cap; termination is then progress-driven (doom-loop guards, budget ceiling) only.",
    )
    tool_mutation_policy: Literal["append_only", "frozen", "unbounded"] = Field(
        default="append_only",
        description=(
            "工具数组变更策略：append_only 顺序稳定+只追加（保护好前缀缓存）；"
            "frozen 需预枚举全部能力；unbounded 放弃缓存红利。"
        ),
    )
    context: ContextConfig = Field(default_factory=ContextConfig)

    # 单任务成本上限（USD）——loop 防护第 3 层：唯一硬天花板。
    # 每轮 LLM 用量按 budget_pricing 费率折算累计进
    # tool_loop_state.total_usd_cost（随 checkpoint 持久化），
    # should_continue 路径 B 消费：>90% 预警注入一次、超限终止。
    # None（默认）= 不设上限；打磨型无限循环（任何进展定义下都在
    # "前进"）只有这一层能兜住。
    max_budget_usd: float | None = Field(
        default=None,
        description="Max USD cost allowed for a single task run (layer-3 hard ceiling). None disables.",
    )
    budget_pricing: BudgetPricingConfig = Field(
        default_factory=BudgetPricingConfig,
        description="USD/Mtok rates used to accrue tool_loop_state.total_usd_cost from LLM usage.",
    )

    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    run_loop: RunLoopTuningConfig = Field(default_factory=RunLoopTuningConfig)
    control: CompletionControlConfig = Field(default_factory=CompletionControlConfig)

    vision: VisionConfig = Field(default_factory=VisionConfig)
    evolution: EvolutionConfig = Field(default_factory=EvolutionConfig)
    subagent: SubAgentConfig = Field(default_factory=SubAgentConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    affinity: AffinityConfig = Field(default_factory=AffinityConfig)
    provider_specs: ProviderModelSpecs = Field(default_factory=ProviderModelSpecs)

    model_config = {"populate_by_name": True}
