"""Trajectory domain data models — pure data, no behaviour.

Lives in contracts (L0) so that Protocol definitions can reference
RequestAuditRecord, TrajectoryCell, etc. without depending on
engine (L2) or persistence (L3).  See SDD §5.1.3.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# ---------------------------------------------------------------------------
# Per-iteration audit record (4+1 layers side-by-side)
# ---------------------------------------------------------------------------

class RequestAuditRecord(BaseModel):
    """Strict schema-validated four-layer snapshot.

    Extra='forbid': any future code that tries to stash an ad-hoc field here
    without updating the schema will *fail-fast* at write time — preventing
    silent schema drift that leads to mass degradation on next upgrade.
    """

    model_config = {"extra": "forbid"}

    # every serialised record carries integer $schema for evolution.
    SCHEMA_VERSION: ClassVar[int] = 1
    schema_version: int = Field(default=1, ge=1)

    # ── identity ────────────────────────────────────────────────────────
    session_id: str = Field(min_length=1)
    iteration: int = Field(ge=0)
    turn: int | None = Field(default=None, ge=0)
    started_at_ms: int = Field(ge=1_700_000_000_000)
    completed_at_ms: int | None = Field(default=None, ge=1_700_000_000_000)
    status: Literal["running", "ok", "error", "aborted", "timeout", "orphaned"] = "running"

    # ── 0.5 BEFORE projector: raw tree.order input to MessageProjector ──
    projector_input_order_count: int = 0
    projector_input_entry_types: tuple[str, ...] = ()
    projector_input_head_seq: tuple[int, ...] = ()
    projector_input_tail_seq: tuple[int, ...] = ()
    projector_input_tail_types: tuple[str, ...] = ()

    # ── ① AFTER MessageProjector: surface (projector output) ───────────
    projector_msg_count: int = 0
    projector_msg_roles: tuple[str, ...] = ()
    projector_head: tuple[str, ...] = ()
    projector_tail: tuple[str, ...] = ()
    projector_appended_msg_count: int = 0
    projector_user_message_preview: str | None = None
    projector_tool_results_preview: tuple[str, ...] = ()
    projector_meta: dict = Field(default_factory=dict)

    # ── ② AssembleCoordinator.assemble() entry messages ────────────────
    assemble_entry_msg_count: int = 0
    assemble_entry_roles: tuple[str, ...] = ()
    assemble_entry_head: tuple[str, ...] = ()
    assemble_entry_tail: tuple[str, ...] = ()
    assemble_entry_meta_keys: tuple[str, ...] = ()
    assemble_entry_meta_values_hash: str = ""

    # ── ③ After PreRequestPipeline.run (budget + microcompact) ─────────
    pipeline_out_msg_count: int = 0
    pipeline_out_roles: tuple[str, ...] = ()
    pipeline_out_head: tuple[str, ...] = ()
    pipeline_out_tail: tuple[str, ...] = ()
    pipeline_out_budget_summary: dict[str, int] = Field(default_factory=dict)
    pipeline_out_compressors_used: tuple[str, ...] = ()
    pipeline_out_compaction_happened: bool = False
    pipeline_out_compaction_summary: str | None = None

    # ── ④ PackedLLMRequest (provider-bound final messages) ─────────────
    packer_final_msg_count: int = 0
    packer_final_roles: tuple[str, ...] = ()
    packer_final_head: tuple[str, ...] = ()
    packer_final_tail: tuple[str, ...] = ()
    packer_final_tokens: int | None = Field(default=None, ge=0)
    packer_final_model: str = ""
    packer_final_system_prompt_len: int = 0
    packer_final_system_prompt_sha1_16: str = ""
    packer_final_system_prompt_text: str | None = None
    packer_final_system_prompt_budget: int | None = Field(default=None, ge=0)
    packer_final_tool_schema_total_chars: int | None = Field(default=None, ge=0)
    packer_final_response_format_text: str | None = None
    packer_final_tool_schema_text: str | None = None

    # ── LLM response ────────────────────────────────────────────────────
    llm_provider: str | None = None
    llm_model: str | None = None
    finish_reason: str | None = None
    usage_input_tokens: int | None = Field(default=None, ge=0)
    usage_output_tokens: int | None = Field(default=None, ge=0)
    usage_cache_read_tokens: int | None = Field(default=None, ge=0)
    usage_cache_write_tokens: int | None = Field(default=None, ge=0)
    usage_reasoning_tokens: int | None = Field(default=None, ge=0)
    ttft_ms: int | None = Field(default=None, ge=0)
    total_ms: int | None = Field(default=None, ge=0)
    assistant_text_preview: str | None = None
    tool_calls_summary: tuple[str, ...] = ()

    # ── error capture ──────────────────────────────────────────────────
    error: str | None = None
    error_stack: str | None = None
    _parse_warning: str | None = None

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _consistent_counts(self) -> RequestAuditRecord:
        n = self.projector_input_order_count
        if len(self.projector_input_entry_types) > 0 and len(self.projector_input_entry_types) != n:
            raise ValueError(
                f"projector_input: order_count={n} but len(entry_types)={len(self.projector_input_entry_types)}. "
                "Rejecting to avoid false 'tree had X entries' conclusions."
            )
        four_layers = (
            ("projector_msg_count", "projector_msg_roles", "projector_head", "projector_tail"),
            ("assemble_entry_msg_count", "assemble_entry_roles", "assemble_entry_head", "assemble_entry_tail"),
            ("pipeline_out_msg_count", "pipeline_out_roles", "pipeline_out_head", "pipeline_out_tail"),
            ("packer_final_msg_count", "packer_final_roles", "packer_final_head", "packer_final_tail"),
        )
        for cnt_field, roles_field, head_field, tail_field in four_layers:
            n = getattr(self, cnt_field)
            roles = getattr(self, roles_field)
            head = getattr(self, head_field)
            tail = getattr(self, tail_field)
            if len(roles) != n:
                raise ValueError(
                    f"{roles_field}: msg_count={n} but len(roles)={len(roles)}. "
                    "Record rejected to avoid false-positive '4 layers equal'."
                )
            if len(head) > n or len(tail) > n:
                raise ValueError(f"{roles_field}: head/tail longer than count={n}")
        if self.total_ms is not None and self.total_ms < 0:
            raise ValueError("total_ms cannot be negative")
        if (
            self.completed_at_ms is not None
            and self.started_at_ms > self.completed_at_ms
        ):
            raise ValueError("started_at_ms > completed_at_ms")
        return self

    @field_validator("error_stack")
    @classmethod
    def _cap_stack(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v[:4000]

    @field_validator("schema_version")
    @classmethod
    def _enforce_schema_version(cls, v: int) -> int:
        if v != RequestAuditRecord.SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema version {v} (expected {RequestAuditRecord.SCHEMA_VERSION}). "
                "Reader must be upgraded or forward-compat path triggered."
            )
        return v

    @field_validator("error", "finish_reason", "llm_provider", "llm_model",
                     "packer_final_model", "packer_final_system_prompt_sha1_16",
                     "assemble_entry_meta_values_hash")
    @classmethod
    def _cap_short(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v[:256]

    @field_validator("packer_final_system_prompt_text", "packer_final_response_format_text")
    @classmethod
    def _cap_large_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if len(v) > 131072:
            return v[:131072] + f"...[truncated from {len(v)}B]"
        return v

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def hash_system_prompt(prompt: str | None) -> tuple[int, str]:
        if prompt is None:
            return 0, ""
        size = len(prompt)
        h = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return size, h[:16]

    @staticmethod
    def hash_meta_values(values: list[str] | tuple[str, ...]) -> str:
        if not values:
            return ""
        joined = "\x00".join(str(v) for v in values)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Snapshot (session aggregate)
# ---------------------------------------------------------------------------

class TrajectorySnapshotMeta(BaseModel):
    model_config = {"extra": "forbid"}

    record_count: int = Field(ge=0)
    turn_count_estimate: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    gap_ratio: float = Field(ge=0.0, le=1.0)
    orphan_count: int = Field(ge=0)
    gap_warning: bool = False
    zstd_compressed_record_count: int = Field(ge=0)


class TrajectorySnapshot(BaseModel):
    model_config = {"extra": "forbid"}

    session_id: str
    records: list[RequestAuditRecord]
    meta: TrajectorySnapshotMeta


# ---------------------------------------------------------------------------
# DSH-aligned per-conversation-event cell model
# ---------------------------------------------------------------------------

class TrajectoryCell(BaseModel):
    """One DSH-style trajectory cell = one conversation-tree node."""
    model_config = ConfigDict(extra="forbid")

    cell_id: str
    session_id: str
    entry_id: str
    seq: int
    turn: int

    kind: str
    role: str

    content_preview: str | None = None
    tool_calls_summary: tuple[str, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None

    input_detail: str | None = None
    output_detail: str | None = None
    thinking_detail: str | None = None
    model: str | None = None
    finish_reason: str | None = None
    usage_input_tokens: int | None = None
    usage_output_tokens: int | None = None
    usage_cache_read_tokens: int | None = None
    usage_cache_write_tokens: int | None = None
    usage_reasoning_tokens: int | None = None
    ttft_ms: float | None = None
    decoding_ms: float | None = None
    is_error: bool = False
    schema_detail: str | None = None

    started_at_ms: int
    completed_at_ms: int | None = None
    iteration: int | None = None
    error: str | None = None


class TrajectoryCellSnapshot(BaseModel):
    """API response shape for GET /sessions/{id}/trajectory/cells."""
    model_config = {"extra": "forbid"}

    session_id: str
    cells: list[TrajectoryCell]
    latest_seq: int = 0


# ---------------------------------------------------------------------------
# Projection state & pure functions (moved from engine.trajectory.projection)
# ---------------------------------------------------------------------------

STATE_VERSION: int = 1  # bump iff `state` key layout changes; triggers full re-fold


@dataclass
class TrajectoryProjectionState:
    version: int = STATE_VERSION
    session_id: str | None = None
    records: list[RequestAuditRecord] = field(default_factory=list)
    pending: RequestAuditRecord | None = None
    turn_counter: int | None = None
    last_iter: int | None = None
    gap_count: int = 0
    orphan_count: int = 0
    last_flushed_count: int = -1

    def is_dirty(self) -> bool:
        """True iff view() output has changed since last mark_clean()."""
        return (
            len(self.records) != self.last_flushed_count
            or self.pending is not None
        )

    def mark_clean(self) -> None:
        self.last_flushed_count = len(self.records)


def init_state(session_id: str) -> TrajectoryProjectionState:
    """Create a blank projection state for a session."""
    return TrajectoryProjectionState(session_id=session_id)


def _clone_record(r: RequestAuditRecord) -> RequestAuditRecord:
    """Deep-ish clone so we don't mutate a previous state's record ref."""
    return r.model_copy(deep=True)


def _estimate_turn_count(rs: list[RequestAuditRecord]) -> int:
    turns: set[int] = set()
    for r in rs:
        if r.turn is not None:
            turns.add(r.turn)
    return len(turns)


def _is_gap_record(r: RequestAuditRecord) -> bool:
    """A record is 'gapped' if it's missing at least one expected layer."""
    if r.status in {"running", "orphaned"}:
        return False
    if not r.assemble_entry_msg_count:
        return True
    if not r.pipeline_out_msg_count:
        return True
    if not r.packer_final_msg_count:
        return True
    return False


def view(
    state: TrajectoryProjectionState,
    *,
    limit: int | None = None,
    ascending: bool = True,
) -> TrajectorySnapshot:
    """Compute the published TrajectorySnapshot view (pure)."""
    all_records = list(state.records)
    if state.pending is not None:
        pending = _clone_record(state.pending)
        pending.status = "running" if pending.status == "running" else pending.status
    else:
        pending = None

    sorted_records = sorted(
        all_records,
        key=lambda r: ((r.turn or -1), r.iteration),
    )
    if not ascending:
        sorted_records = sorted(
            all_records,
            key=lambda r: (-(r.turn or -1), r.iteration),
        )

    if limit is not None and limit > 0:
        if limit < len(sorted_records):
            sorted_records = sorted_records[:limit]

    total = len(sorted_records)
    gap_cnt = sum(1 for r in sorted_records if _is_gap_record(r))
    orphan_cnt = sum(1 for r in sorted_records if r.status == "orphaned")
    ratio = (gap_cnt / total) if total else 0.0
    meta = TrajectorySnapshotMeta(
        record_count=total,
        turn_count_estimate=_estimate_turn_count(sorted_records),
        gap_count=gap_cnt,
        gap_ratio=ratio,
        orphan_count=orphan_cnt,
        gap_warning=(ratio >= 0.2),
        zstd_compressed_record_count=0,
    )
    return TrajectorySnapshot(
        session_id=state.session_id or "",
        records=tuple(sorted_records),
        meta=meta,
    )


__all__ = [
    # 领域数据模型
    "RequestAuditRecord",
    "TrajectoryCell",
    "TrajectoryCellSnapshot",
    "TrajectorySnapshot",
    "TrajectorySnapshotMeta",
    # 投影状态 & 纯函数
    "STATE_VERSION",
    "TrajectoryProjectionState",
    "init_state",
    "view",
]
