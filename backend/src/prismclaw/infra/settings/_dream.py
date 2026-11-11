from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ._memory import CuratorSettings


class DreamSettings(BaseSettings):
    enabled: bool = True
    idle_check_interval_seconds: int = Field(300)
    idle_threshold_seconds: int = Field(1800)
    nightly_check_interval_seconds: int = Field(1800)
    nightly_timezone: str = Field("local")
    nightly_start_hour_local: int = Field(1)
    nightly_end_hour_local: int = Field(5)
    nightly_catch_up_grace_minutes: int = Field(90)
    nightly_max_sessions: int = Field(10)
    multi_worker_scan_policy: Literal["all", "leader_only"] = Field(
        "leader_only"
    )
    publish_channel: Literal["direct", "shadow", "canary"] | None = Field(
        None
    )
    memory_publish_channel: Literal["direct", "shadow", "canary"] | None = Field(
        None
    )
    artifact_publish_channel: Literal["direct", "shadow", "canary"] | None = Field(
        None
    )
    canary_rollout_percentage: int = Field(0)
    canary_rollout_salt: str = Field("dream-canary")
    shadow_publish_enabled: bool = Field(False)
    shadow_memory_publish_enabled: bool | None = Field(None)
    shadow_artifact_publish_enabled: bool | None = Field(None)
    # SDD B3 (docs/memory-dream-sdd.md §5.3): three-tier consolidation —
    # light (deterministic dedup, zero LLM) / mid (evidence bookkeeping,
    # zero LLM) / deep (evidence-gated semantic merge). Replaces the legacy
    # nightly LLM-merge phases when enabled.
    tiers_enabled: bool = Field(False)
    # CONTEST quarantine exit: no validation evidence after N days → decayed.
    contest_exit_days: int = Field(30)
    # Deep-merge evidence gate: BOTH sides need >= N positive signals.
    deep_merge_min_positive: int = Field(1)
    # E4: 纠正自学习。当 active 条目有 >=2 strong_negative 信号且同 entity_key
    # 有较新条目时，提炼策略记忆（L3 procedure）SUPERSEDE 旧条目。
    correction_learning_enabled: bool = Field(False)

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_DREAM_",
        populate_by_name=True,
        extra="ignore",
    )

    @property
    def effective_shadow_memory_publish_enabled(self) -> bool:
        if self.shadow_memory_publish_enabled is None:
            return self.shadow_publish_enabled
        return self.shadow_memory_publish_enabled

    @property
    def effective_shadow_artifact_publish_enabled(self) -> bool:
        if self.shadow_artifact_publish_enabled is None:
            return self.shadow_publish_enabled
        return self.shadow_artifact_publish_enabled

    @property
    def effective_memory_publish_channel(self) -> Literal["direct", "shadow", "canary"]:
        if self.memory_publish_channel is not None:
            return self.memory_publish_channel
        if self.publish_channel is not None:
            return self.publish_channel
        return "shadow" if self.effective_shadow_memory_publish_enabled else "direct"

    @property
    def effective_artifact_publish_channel(self) -> Literal["direct", "shadow", "canary"]:
        if self.artifact_publish_channel is not None:
            return self.artifact_publish_channel
        if self.publish_channel is not None:
            return self.publish_channel
        return "shadow" if self.effective_shadow_artifact_publish_enabled else "direct"

    @property
    def effective_canary_rollout_percentage(self) -> int:
        return max(0, min(100, int(self.canary_rollout_percentage)))


class EvolutionSettings(BaseSettings):
    """Self-evolution SOP extraction configuration."""

    enabled: bool = True
    extraction_model: str | None = None
    max_candidates: int = 100
    min_tool_calls_for_extraction: int = Field(2)
    dedup_overlap_threshold: float = 0.6
    max_sops_per_extraction: int = 5
    description_max_chars: int = 150
    procedure_max_chars: int = 5000
    # SDD A4 (docs/memory-dream-sdd.md §5.4): batched fact extraction over
    # no-tool chat turns at session end. Default off until B1/B2 lands.
    capture_fullturn_enabled: bool = Field(False)
    curator: CuratorSettings = Field(default_factory=lambda: CuratorSettings())
    dream: DreamSettings = Field(default_factory=lambda: DreamSettings())

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_EVOLUTION_",
        populate_by_name=True,
        extra="ignore",
    )
