from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal
try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

# Single source of truth — imported from _preservation_rules.py.
# The old dual-copy cycle is resolved by making _preservation_rules.py
# a pure-constants module with no internal imports.
try:
    from prismclaw.engine.compression._preservation_rules import IDENTIFIER_PRESERVATION_INSTRUCTIONS
except ImportError:
    # Schema layer may load before engine layer in some test/CLI paths
    IDENTIFIER_PRESERVATION_INSTRUCTIONS = (
        "在摘要中逐字保留标识符，用户事实逐字带入第10节，作废需求列在第11节。"
    )


class WorkspaceConfig(BaseModel):
    workspaces: dict[str, str] = Field(default_factory=lambda: {"default": "."})

    def resolve_path(self, workspace_id: str) -> Path:
        raw = self.workspaces.get(workspace_id) or self.workspaces.get("default", ".")
        return Path(raw).expanduser().resolve()


class TimeoutConfig(BaseModel):
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
    "你的任务是详细总结此前的对话，重点关注用户的明确请求、所做的关键决策和产出。\n\n"
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
    "3. 关键文件与产出\n"
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
    f"{IDENTIFIER_PRESERVATION_INSTRUCTIONS}\n"
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


# IDENTIFIER_PRESERVATION_INSTRUCTIONS is imported from _preservation_rules.py above.
# Pydantic reads default values from COMPACT_SUMMARY_DEFAULT_TEMPLATE
# (which embeds IDENTIFIER_PRESERVATION_INSTRUCTIONS) when constructing
# CompactPromptConfig instances.


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

    轮次 / 墙钟上限不在这里：它们由 ``effort`` 档位（RunRequest.effort）
    解析，由 BudgetGuard 统一读取。
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


class StuckConfig(BaseModel):
    """卡死检测配置（doom-loop 签名冻结）。"""

    signature_warn: int = Field(
        default=3,
        description=(
            "同一工具调用签名（含结果指纹）连续重复达到此值时触发 soft warn；"
            "超过此值注入结构化纠正；达到 2× 时冻结该签名（钳制在 12 条历史窗口内）。"
            "0 禁用整条阶梯。"
        ),
    )


class RunLoopTuningConfig(BaseModel):
    search_intent_tools: list[str] = Field(
        default_factory=lambda: ["web-search-mcp__search"],
        description="Tool names treated as search/fetch intent for wrapping-phase freeze.",
    )
    stuck: StuckConfig = Field(
        default_factory=StuckConfig,
        description="Doom-loop signature freeze thresholds.",
    )

    # run 级终态唯一由 CompletionKernel 裁决（预算 / 完成契约 / 恢复熔断）。
    # 工具循环阶梯（doom-loop 签名冻结）只产引导，不终止。
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
    # 压缩后文件重注入：单文件 token 预算（全局 = 本值 × compaction.reinject_max_files，不另设键）
    reinject_file_tokens: int = Field(3200, ge=0)
    # 技能 preload 注入 token 上限
    skill_preload: int = Field(7500, ge=0)


class ContextBudgetConfig(BaseModel):
    """上下文窗口与分区预算（absolute 模式）。

    收敛了原先分散在 ``agent.max_context_tokens`` 与 ``agent.promptBudget``
    两处的配置。history 可用空间 = 窗口 - 冻结区 - 动态区 - 输出预留。
    """

    model_config = {"populate_by_name": True}

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
        """固定分区之和不得超过窗口（分区溢出与显式预留溢出一并校验）。"""
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
        """
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
        # 请求层的 max_tokens 钳制兜底）。
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


class EffortTierConfig(BaseModel):
    """单个 effort 档位的预算配额。

    steps / tool_calls 是硬上限；soft_s 只触发收尾信号，hard_s 才终止。
    派生值（reserve_s / cap_s / converge_tools）不配置，由运行时按
    hard_s 与 tool_calls 计算。
    """

    steps: int = Field(..., ge=1, description="Max LLM steps (stream_llm calls) for this tier.")
    tool_calls: int = Field(..., ge=1, description="Max tool-call attempts (parallel N counts as N; denied included).")
    soft_s: float = Field(..., ge=0.0, description="Wall-clock soft deadline (wrap-up signal only).")
    hard_s: float = Field(..., ge=0.0, description="Wall-clock hard deadline (termination).")


class EffortConfig(BaseModel):
    """三档 effort 预算。调用方通过 RunRequest.effort 选档；
    RunRequest.long_running 对所选档位放大（steps×3 上限 30、hard_s×2 上限 1800）。"""

    quick: EffortTierConfig = EffortTierConfig(steps=3, tool_calls=6, soft_s=45.0, hard_s=90.0)
    standard: EffortTierConfig = EffortTierConfig(steps=6, tool_calls=12, soft_s=150.0, hard_s=300.0)
    deep: EffortTierConfig = EffortTierConfig(steps=15, tool_calls=24, soft_s=420.0, hard_s=900.0)


class AgentRunConfig(BaseModel):
    effort: EffortConfig = Field(
        default_factory=EffortConfig,
        description="Effort tier budgets (quick/standard/deep). Selected per-run via RunRequest.effort.",
    )
    tool_mutation_policy: Literal["append_only", "frozen", "unbounded"] = Field(
        default="append_only",
        description=(
            "工具数组变更策略：append_only 顺序稳定+只追加（保护好前缀缓存）；"
            "frozen 需预枚举全部能力；unbounded 放弃缓存红利。"
        ),
    )
    context: ContextConfig = Field(default_factory=ContextConfig)

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
