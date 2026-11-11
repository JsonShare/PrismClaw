from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_MEMORY_BASE_DIR = "~/.prismclaw/memory"
_FALLBACK_MEMORY_BASE_DIR = "~/.cache/prismclaw/memory"


def _dir_supports_writes(path: Path) -> bool:
    probe = path / f".prismclaw-write-probe-{os.getpid()}"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        try:
            probe.unlink(missing_ok=True)
        except Exception:
            pass
        return False


class MemorySettings(BaseSettings):
    base_dir: str = _DEFAULT_MEMORY_BASE_DIR
    l1_max_entries: int = 30
    l1_max_chars: int = 3000
    l1_ttl_seconds: int = 2_592_000  # 30 days
    search_l2_quota: int = 3
    search_l3_quota: int = 2
    archive_max_results: int = 5
    archive_min_similarity: float = 0.5
    archive_min_results: int = 1
    archive_enabled: bool = True
    # 意图偏置（context_router 动态配额）：key 为意图×目标维度，value 为加项。
    # 默认值等价于 DynamicContextRouter 里的硬编码加项（prefers_tooling / requires_grounding
    # / cold_start / follow_up）。
    intent_bias: dict[str, int] = Field(
        default_factory=lambda: {
            "prefers_tooling_l3": 3,
            "prefers_tooling_l2": -1,
            "prefers_tooling_archive": -1,
            "requires_grounding_l2": 3,
            "requires_grounding_archive": 2,
            "requires_grounding_l3": -1,
            "cold_start_l3": 1,
            "follow_up_l2": -2,
            "follow_up_l3": -1,
        }
    )
    naming_policy: Literal["human", "hash"] = Field("human")
    # SDD B1 (docs/memory-dream-sdd.md §5.1): retrieval attribution —
    # injection bookkeeping + use_count writeback + validation signals.
    attribution_enabled: bool = Field(False)
    # SDD B2 (§5.2): memory write arbitration gateway.
    # off = legacy direct store; shadow = arbitrate + audit only (production
    # behavior unchanged); active = arbitrate + execute decisions.
    gateway_mode: Literal["off", "shadow", "active"] = Field("off")
    # SUPERSEDE stays shadow-gated until decision gate 2 (manual sampling
    # >=80% correct); when False a SUPERSEDE verdict degrades to ADD.
    gateway_supersede_active: bool = Field(False)
    gateway_neighbor_limit: int = Field(3)
    # SDD B4 (§5.5): retrieval ranking v2 — multiplicative composite
    # (rank-normalized relevance × decay × log-capped use × confidence)
    # replacing the legacy lexicographic tuple key in the heuristic selector.
    ranking_v2_enabled: bool = Field(False)
    # Decay uncertainty period: compute-and-record only (counterfactual
    # ordering in metrics/logs), ranking unchanged until manually released.
    ranking_decay_apply: bool = Field(False)
    # Abstention: shadow records "would have abstained" in metrics; hard
    # (empty selection) only after gate-3 baseline (>=1000 injections).
    abstention_hard_enabled: bool = Field(False)
    abstention_score_threshold: float = Field(0.1)
    # SDD B5 (§5.9): scope routing — agent-scope entries leave the default
    # retrieval path (dedicated channel deferred, open question #2). Write-
    # time stamping happens regardless; this gates read-side enforcement.
    # Near-no-op today: no writers produce agent-scope entries yet.
    scope_routing_enabled: bool = Field(False)
    # E1: 记忆写入前安全拦截器（不可见 Unicode 隐写扫描 + 注入检测 + DLP 脱敏）。
    # notary=None 时 store() 零开销。
    notary_enabled: bool = Field(False)
    # E3: 遗忘经济学预算。0=关闭（默认），>0 时 active 条目超过此数触发降级。
    memory_forgetting_budget_entries: int = Field(0)
    # E5: 记忆图谱关系。graph_enabled 控制边存储和遍历。
    memory_graph_enabled: bool = Field(False)

    model_config = SettingsConfigDict(env_prefix="PRISMCLAW_MEMORY_")

    def model_post_init(self, __context) -> None:
        configured = (self.base_dir or "").strip() or _DEFAULT_MEMORY_BASE_DIR
        try:
            configured_path = Path(configured).expanduser()
            default_path = Path(_DEFAULT_MEMORY_BASE_DIR).expanduser()
            fallback_path = Path(_FALLBACK_MEMORY_BASE_DIR).expanduser()
        except Exception:
            return

        if configured_path != default_path:
            return
        if _dir_supports_writes(configured_path):
            return
        if not _dir_supports_writes(fallback_path):
            return

        logging.getLogger("prismclaw.settings").warning(
            "configured memory.base_dir points to default path %s but it is not writable; "
            "using fallback memory directory %s instead",
            configured_path,
            fallback_path,
        )
        self.base_dir = str(fallback_path)


class CuratorSettings(BaseSettings):
    """Curator 生命周期管理配置。"""

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
    llm_review_actions: list[str] = Field(
        default_factory=lambda: ["promote"]
    )
    llm_review_max_batch: int = Field(20)

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_CURATOR_",
        populate_by_name=True,
        extra="ignore",
    )
