"""Trajectory audit PostgreSQL DDL and SQL constants.

Follows the same pattern as ``prismclaw.persistence.user.models``:
  - Raw SQL strings, no SQLAlchemy ORM models
  - Idempotent ``CREATE TABLE IF NOT EXISTS``
  - Uses ``text()`` for parameterised queries with :param_name format

Tables:
  pc_trajectory_session  → One row per session, holds aggregate metadata
  pc_trajectory_record   → One row per LLM iteration (RequestAuditRecord)

Design rationale:
  - **Separate tables**: Session-level metadata (record_count, gap_count)
    doesn't need to be stored with every record — reduces redundancy.
  - **JSONB for complex fields**: Arrays and dicts (entry_types, heads,
    tails, budget_summary) are stored as JSONB for efficient querying
    and indexing.
  - **Partial system prompt**: TEXT for packer_final_system_prompt_text
    — PostgreSQL handles large TEXT natively (no Redis 128KB limit).
  - **Index on session_id + turn**: Supports the common query pattern
    "show all records for session X, grouped by turn".

ALL parameterized queries use SQLAlchemy :param_name format (NOT $1, $2...).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# DDL — Session metadata table
# ---------------------------------------------------------------------------

CREATE_TRAJECTORY_SESSION_TABLE = """
CREATE TABLE IF NOT EXISTS pc_trajectory_session (
    session_id              TEXT        PRIMARY KEY,
    record_count            INTEGER     NOT NULL DEFAULT 0,
    turn_count_estimate     INTEGER     NOT NULL DEFAULT 0,
    gap_count               INTEGER     NOT NULL DEFAULT 0,
    gap_ratio               DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    orphan_count            INTEGER     NOT NULL DEFAULT 0,
    gap_warning             BOOLEAN     NOT NULL DEFAULT FALSE,
    zstd_compressed_record_count INTEGER NOT NULL DEFAULT 0,
    last_updated_ms         BIGINT      NOT NULL DEFAULT 0,
    created_at              TIMESTAMP   NOT NULL DEFAULT now(),
    updated_at              TIMESTAMP   NOT NULL DEFAULT now()
)
"""

# ---------------------------------------------------------------------------
# DDL — Record table (one row per LLM iteration)
# ---------------------------------------------------------------------------

CREATE_TRAJECTORY_RECORD_TABLE = """
CREATE TABLE IF NOT EXISTS pc_trajectory_record (
    id                      BIGSERIAL   PRIMARY KEY,
    session_id              TEXT        NOT NULL,
    iteration               INTEGER     NOT NULL,
    turn                    INTEGER,
    started_at_ms           BIGINT      NOT NULL,
    completed_at_ms         BIGINT,
    status                  TEXT        NOT NULL DEFAULT 'running'
                                        CHECK (status IN ('running', 'ok', 'error', 'aborted', 'timeout', 'orphaned')),

    -- Layer 0.5: projector input
    projector_input_order_count INTEGER  NOT NULL DEFAULT 0,
    projector_input_entry_types JSONB    NOT NULL DEFAULT '[]',
    projector_input_head_seq    JSONB    NOT NULL DEFAULT '[]',
    projector_input_tail_seq    JSONB    NOT NULL DEFAULT '[]',
    projector_input_tail_types  JSONB    NOT NULL DEFAULT '[]',

    -- Layer ①: projector output
    projector_msg_count     INTEGER     NOT NULL DEFAULT 0,
    projector_msg_roles     JSONB       NOT NULL DEFAULT '[]',
    projector_head          JSONB       NOT NULL DEFAULT '[]',
    projector_tail          JSONB       NOT NULL DEFAULT '[]',
    projector_appended_msg_count INTEGER NOT NULL DEFAULT 0,
    projector_user_message_preview TEXT,
    projector_tool_results_preview JSONB NOT NULL DEFAULT '[]',
    projector_meta          JSONB       NOT NULL DEFAULT '{}',

    -- Layer ②: assemble entry
    assemble_entry_msg_count INTEGER    NOT NULL DEFAULT 0,
    assemble_entry_roles    JSONB       NOT NULL DEFAULT '[]',
    assemble_entry_head     JSONB       NOT NULL DEFAULT '[]',
    assemble_entry_tail     JSONB       NOT NULL DEFAULT '[]',
    assemble_entry_meta_keys JSONB      NOT NULL DEFAULT '[]',
    assemble_entry_meta_values_hash TEXT NOT NULL DEFAULT '',

    -- Layer ③: pipeline output
    pipeline_out_msg_count  INTEGER     NOT NULL DEFAULT 0,
    pipeline_out_roles      JSONB       NOT NULL DEFAULT '[]',
    pipeline_out_head       JSONB       NOT NULL DEFAULT '[]',
    pipeline_out_tail       JSONB       NOT NULL DEFAULT '[]',
    pipeline_out_budget_summary JSONB   NOT NULL DEFAULT '{}',
    pipeline_out_compressors_used JSONB NOT NULL DEFAULT '[]',
    pipeline_out_compaction_happened BOOLEAN NOT NULL DEFAULT FALSE,
    pipeline_out_compaction_summary TEXT,

    -- Layer ④: packer final
    packer_final_msg_count  INTEGER     NOT NULL DEFAULT 0,
    packer_final_roles      JSONB       NOT NULL DEFAULT '[]',
    packer_final_head       JSONB       NOT NULL DEFAULT '[]',
    packer_final_tail       JSONB       NOT NULL DEFAULT '[]',
    packer_final_tokens     INTEGER,
    packer_final_model      TEXT        NOT NULL DEFAULT '',
    packer_final_system_prompt_len INTEGER NOT NULL DEFAULT 0,
    packer_final_system_prompt_sha1_16 TEXT NOT NULL DEFAULT '',
    packer_final_system_prompt_text    TEXT,
    packer_final_system_prompt_budget  INTEGER,
    packer_final_tool_schema_total_chars INTEGER,
    packer_final_response_format_text  TEXT,
    packer_final_tool_schema_text TEXT,

    -- LLM response
    llm_provider            TEXT,
    llm_model               TEXT,
    finish_reason           TEXT,
    usage_input_tokens      INTEGER,
    usage_output_tokens     INTEGER,
    usage_cache_read_tokens INTEGER,
    usage_cache_write_tokens INTEGER,
    usage_reasoning_tokens  INTEGER,
    ttft_ms                 INTEGER,
    total_ms                INTEGER,
    assistant_text_preview  TEXT,
    tool_calls_summary      JSONB       NOT NULL DEFAULT '[]',

    -- Error
    error                   TEXT,
    error_stack             TEXT,

    created_at              TIMESTAMP   NOT NULL DEFAULT now(),
    updated_at              TIMESTAMP   NOT NULL DEFAULT now(),

    CONSTRAINT fk_pc_trajectory_record_session
        FOREIGN KEY (session_id) REFERENCES pc_trajectory_session(session_id) ON DELETE CASCADE
)
"""

# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

INDEXES = [
    # Common query: get all records for a session, ordered by turn/iteration
    "CREATE INDEX IF NOT EXISTS idx_traj_record_session_turn ON pc_trajectory_record (session_id, turn, iteration)",
    # Query by status for debugging
    "CREATE INDEX IF NOT EXISTS idx_traj_record_status ON pc_trajectory_record (status)",
    # Query by time range
    "CREATE INDEX IF NOT EXISTS idx_traj_record_started_at ON pc_trajectory_record (started_at_ms)",
    # GIN index for JSONB array queries (if needed later)
    "CREATE INDEX IF NOT EXISTS idx_traj_record_projector_entry_types ON pc_trajectory_record USING GIN (projector_input_entry_types)",
 # cell table index — common query "all cells for session, by seq"
    "CREATE INDEX IF NOT EXISTS idx_trajectory_cell_session ON pc_trajectory_cell (session_id, seq)",
    # Error-marking lookups: (session_id, turn, iteration, kind)
    "CREATE INDEX IF NOT EXISTS idx_trajectory_cell_turn_iter ON pc_trajectory_cell (session_id, turn, iteration, kind)",
]

INDEX_TRAJECTORY_CELL_TURN_ITER = (
    "CREATE INDEX IF NOT EXISTS idx_trajectory_cell_turn_iter "
    "ON pc_trajectory_cell (session_id, turn, iteration, kind)"
)

# ---------------------------------------------------------------------------
# Schema migrations tracking table
# ---------------------------------------------------------------------------

CREATE_SCHEMA_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS pc_schema_migrations (
    migration_name TEXT PRIMARY KEY,
    applied_at     TIMESTAMP NOT NULL DEFAULT now()
)
"""

CHECK_MIGRATION = """
SELECT 1 FROM pc_schema_migrations WHERE migration_name = :name
"""

RECORD_MIGRATION = """
INSERT INTO pc_schema_migrations (migration_name)
VALUES (:name)
ON CONFLICT (migration_name) DO NOTHING
"""

# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

TOUCH_UPDATED_AT = [
    """
    CREATE OR REPLACE FUNCTION pc_traj_touch_updated_at() RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = now();
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """,
    "DROP TRIGGER IF EXISTS trg_traj_session_touch ON pc_trajectory_session;",
    "CREATE TRIGGER trg_traj_session_touch BEFORE UPDATE ON pc_trajectory_session "
    "FOR EACH ROW EXECUTE FUNCTION pc_traj_touch_updated_at();",
    "DROP TRIGGER IF EXISTS trg_traj_record_touch ON pc_trajectory_record;",
    "CREATE TRIGGER trg_traj_record_touch BEFORE UPDATE ON pc_trajectory_record "
    "FOR EACH ROW EXECUTE FUNCTION pc_traj_touch_updated_at();",
]

# ---------------------------------------------------------------------------
# SQL Queries (ALL use :param_name format for SQLAlchemy text())
# ---------------------------------------------------------------------------

UPSERT_SESSION_META = """
INSERT INTO pc_trajectory_session (
    session_id, record_count, turn_count_estimate,
    gap_count, gap_ratio, orphan_count, gap_warning,
    zstd_compressed_record_count, last_updated_ms
) VALUES (
    :session_id, :record_count, :turn_count_estimate,
    :gap_count, :gap_ratio, :orphan_count, :gap_warning,
    :zstd_compressed_record_count, :last_updated_ms
)
ON CONFLICT (session_id) DO UPDATE SET
    record_count = EXCLUDED.record_count,
    turn_count_estimate = EXCLUDED.turn_count_estimate,
    gap_count = EXCLUDED.gap_count,
    gap_ratio = EXCLUDED.gap_ratio,
    orphan_count = EXCLUDED.orphan_count,
    gap_warning = EXCLUDED.gap_warning,
    zstd_compressed_record_count = EXCLUDED.zstd_compressed_record_count,
    last_updated_ms = EXCLUDED.last_updated_ms,
    updated_at = now()
"""

# ============================================================================
# 对齐 per-conversation-event cell table
# ============================================================================

CREATE_TRAJECTORY_CELL_TABLE = """
CREATE TABLE IF NOT EXISTS pc_trajectory_cell (
    cell_id          TEXT        NOT NULL,
    session_id      TEXT        NOT NULL,
    entry_id         TEXT        NOT NULL,
    seq              INTEGER     NOT NULL,
    turn             INTEGER     NOT NULL,
    kind             TEXT        NOT NULL,
    role             TEXT        NOT NULL,
    content_preview  TEXT,
    tool_calls_summary JSONB    NOT NULL DEFAULT '[]',
    tool_call_id     TEXT,
    tool_name        TEXT,
 -- full-content + metric fields (TrajectoryCellProps)
    input_detail     TEXT,
    output_detail    TEXT,
    thinking_detail  TEXT,
    model            TEXT,
    finish_reason    TEXT,
    usage_input_tokens      INTEGER,
    usage_output_tokens     INTEGER,
    usage_cache_read_tokens INTEGER,
    usage_cache_write_tokens INTEGER,
    usage_reasoning_tokens  INTEGER,
    ttft_ms          DOUBLE PRECISION,
    decoding_ms      DOUBLE PRECISION,
    is_error         BOOLEAN     NOT NULL DEFAULT FALSE,
    schema_detail    TEXT,
    started_at_ms    BIGINT      NOT NULL,
    completed_at_ms  BIGINT,
    iteration        INTEGER,
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (cell_id),
    UNIQUE (session_id, entry_id)
);
"""

INDEX_TRAJECTORY_CELL_SESSION = """
CREATE INDEX IF NOT EXISTS idx_trajectory_cell_session
ON pc_trajectory_cell (session_id, seq);
"""

INSERT_CELL = """
INSERT INTO pc_trajectory_cell (
    cell_id, session_id, entry_id, seq, turn, kind, role,
    content_preview, tool_calls_summary, tool_call_id, tool_name,
    input_detail, output_detail, thinking_detail,
    model, finish_reason,
    usage_input_tokens, usage_output_tokens,
    usage_cache_read_tokens, usage_cache_write_tokens,
    usage_reasoning_tokens,
    ttft_ms, decoding_ms,
    is_error, schema_detail,
    started_at_ms, completed_at_ms, iteration, error
) VALUES (
    :cell_id, :session_id, :entry_id, :seq, :turn, :kind, :role,
    :content_preview, :tool_calls_summary, :tool_call_id, :tool_name,
    :input_detail, :output_detail, :thinking_detail,
    :model, :finish_reason,
    :usage_input_tokens, :usage_output_tokens,
    :usage_cache_read_tokens, :usage_cache_write_tokens,
    :usage_reasoning_tokens,
    :ttft_ms, :decoding_ms,
    :is_error, :schema_detail,
    :started_at_ms, :completed_at_ms, :iteration, :error
)
ON CONFLICT (session_id, entry_id) DO UPDATE SET
    seq = EXCLUDED.seq,
    turn = EXCLUDED.turn,
    kind = EXCLUDED.kind,
    role = EXCLUDED.role,
    content_preview = EXCLUDED.content_preview,
    tool_calls_summary = EXCLUDED.tool_calls_summary,
    tool_call_id = EXCLUDED.tool_call_id,
    tool_name = COALESCE(EXCLUDED.tool_name, pc_trajectory_cell.tool_name),
    input_detail = COALESCE(EXCLUDED.input_detail, pc_trajectory_cell.input_detail),
    output_detail = COALESCE(EXCLUDED.output_detail, pc_trajectory_cell.output_detail),
    thinking_detail = COALESCE(EXCLUDED.thinking_detail, pc_trajectory_cell.thinking_detail),
    model = COALESCE(EXCLUDED.model, pc_trajectory_cell.model),
    finish_reason = COALESCE(EXCLUDED.finish_reason, pc_trajectory_cell.finish_reason),
    usage_input_tokens = COALESCE(EXCLUDED.usage_input_tokens, pc_trajectory_cell.usage_input_tokens),
    usage_output_tokens = COALESCE(EXCLUDED.usage_output_tokens, pc_trajectory_cell.usage_output_tokens),
    usage_cache_read_tokens = COALESCE(EXCLUDED.usage_cache_read_tokens, pc_trajectory_cell.usage_cache_read_tokens),
    usage_cache_write_tokens = COALESCE(EXCLUDED.usage_cache_write_tokens, pc_trajectory_cell.usage_cache_write_tokens),
    usage_reasoning_tokens = COALESCE(EXCLUDED.usage_reasoning_tokens, pc_trajectory_cell.usage_reasoning_tokens),
    ttft_ms = COALESCE(EXCLUDED.ttft_ms, pc_trajectory_cell.ttft_ms),
    decoding_ms = COALESCE(EXCLUDED.decoding_ms, pc_trajectory_cell.decoding_ms),
    is_error = EXCLUDED.is_error,
    schema_detail = COALESCE(EXCLUDED.schema_detail, pc_trajectory_cell.schema_detail),
    started_at_ms = EXCLUDED.started_at_ms,
    completed_at_ms = COALESCE(EXCLUDED.completed_at_ms, pc_trajectory_cell.completed_at_ms),
    iteration = EXCLUDED.iteration,
    error = EXCLUDED.error,
    created_at = now()
"""

# NOTE: there is deliberately NO metrics-UPDATE statement for cells — the
# ConversationNodeAdded event carries the complete cell payload and
# insert_cell_pg writes it in one INSERT (see runtime_context.py).

# Failure marking: when an LLM request fails after a partial assistant
# cell was already INSERTed (streaming.py persists partial output before
# _publish_llm_failure), mark that cell. IS NOT DISTINCT FROM keeps the
# match working when iteration is NULL on both sides.
MARK_CELL_ERROR = """
UPDATE pc_trajectory_cell SET
    is_error = TRUE,
    error = COALESCE(:error, error),
    model = COALESCE(:model, model),
    completed_at_ms = COALESCE(:completed_at_ms, completed_at_ms)
WHERE session_id = :session_id AND turn = :turn
  AND iteration IS NOT DISTINCT FROM :iteration
  AND kind = 'message'
"""

# Synthesized error cell for failures with NO partial output (e.g. idle
# timeout before the first token). seq is computed atomically per session
# (MAX+1) so the cell sorts next to its turn — a magic sentinel seq would
# push every error cell to the end of the whole session. Deterministic
# entry_id makes retries idempotent via the (session_id, entry_id)
# UNIQUE constraint handled by INSERT_CELL's ON CONFLICT clause.
INSERT_ERROR_CELL = """
INSERT INTO pc_trajectory_cell (
    cell_id, session_id, entry_id, seq, turn, kind, role,
    content_preview, tool_calls_summary, tool_call_id,
    input_detail, output_detail, thinking_detail,
    model, finish_reason,
    usage_input_tokens, usage_output_tokens,
    usage_cache_read_tokens, usage_cache_write_tokens,
    usage_reasoning_tokens,
    ttft_ms, decoding_ms,
    is_error, schema_detail,
    started_at_ms, completed_at_ms, iteration, error
) VALUES (
    :cell_id, :session_id, :entry_id,
    (SELECT COALESCE(MAX(seq), 0) + 1 FROM pc_trajectory_cell
     WHERE session_id = :session_id),
    :turn, 'message', 'assistant',
    :content_preview, '[]'::jsonb, NULL,
    NULL, :output_detail, NULL,
    :model, NULL,
    NULL, NULL, NULL, NULL, NULL,
    :ttft_ms, NULL,
    TRUE, NULL,
    :started_at_ms, :completed_at_ms, :iteration, :error
)
ON CONFLICT (session_id, entry_id) DO UPDATE SET
    error = EXCLUDED.error,
    is_error = TRUE,
    completed_at_ms = EXCLUDED.completed_at_ms
"""

# System-prompt cell: published by the trajectory service when the prompt
# sha changes across turns (per-turn injected time makes it change every
# turn — that IS the audit value). Same atomic-MAX-seq pattern as
# INSERT_ERROR_CELL; deterministic entry_id keeps retries idempotent.
INSERT_SYSTEM_CELL = """
INSERT INTO pc_trajectory_cell (
    cell_id, session_id, entry_id, seq, turn, kind, role,
    content_preview, tool_calls_summary, tool_call_id,
    input_detail, output_detail, thinking_detail,
    model, finish_reason,
    usage_input_tokens, usage_output_tokens,
    usage_cache_read_tokens, usage_cache_write_tokens,
    usage_reasoning_tokens,
    ttft_ms, decoding_ms,
    is_error, schema_detail,
    started_at_ms, completed_at_ms, iteration, error
) VALUES (
    :cell_id, :session_id, :entry_id,
    (SELECT COALESCE(MAX(seq), 0) + 1 FROM pc_trajectory_cell
     WHERE session_id = :session_id),
    :turn, 'system', 'system',
    :content_preview, '[]'::jsonb, NULL,
    :input_detail, NULL, NULL,
    NULL, NULL,
    NULL, NULL, NULL, NULL, NULL,
    NULL, NULL,
    FALSE, NULL,
    :started_at_ms, :completed_at_ms, :iteration, NULL
)
ON CONFLICT (session_id, entry_id) DO UPDATE SET
    content_preview = EXCLUDED.content_preview,
    input_detail = EXCLUDED.input_detail,
    started_at_ms = EXCLUDED.started_at_ms,
    completed_at_ms = EXCLUDED.completed_at_ms
"""

SELECT_CELLS_BY_SESSION = """
SELECT cell_id, session_id, entry_id, seq, turn, kind, role,
       content_preview, tool_calls_summary, tool_call_id, tool_name,
       input_detail, output_detail, thinking_detail,
       model, finish_reason,
       usage_input_tokens, usage_output_tokens,
       usage_cache_read_tokens, usage_cache_write_tokens,
       usage_reasoning_tokens,
       ttft_ms, decoding_ms,
       is_error, schema_detail,
       started_at_ms, completed_at_ms, iteration, error
FROM pc_trajectory_cell
WHERE session_id = :session_id
ORDER BY turn ASC, (CASE WHEN kind = 'system' THEN 0 ELSE 1 END) ASC, seq ASC
"""

# Incremental poll: rows newer than the client's seq cursor. Cells are
# written complete in one INSERT (single-writer per entry_id), so a pure
# seq predicate is sufficient — there is no post-insert enrichment to re-read.
SELECT_CELLS_INCREMENTAL = """
SELECT cell_id, session_id, entry_id, seq, turn, kind, role,
       content_preview, tool_calls_summary, tool_call_id, tool_name,
       input_detail, output_detail, thinking_detail,
       model, finish_reason,
       usage_input_tokens, usage_output_tokens,
       usage_cache_read_tokens, usage_cache_write_tokens,
       usage_reasoning_tokens,
       ttft_ms, decoding_ms,
       is_error, schema_detail,
       started_at_ms, completed_at_ms, iteration, error
FROM pc_trajectory_cell
WHERE session_id = :session_id
  AND seq > :after_seq
ORDER BY turn ASC, (CASE WHEN kind = 'system' THEN 0 ELSE 1 END) ASC, seq ASC
"""

DELETE_CELLS_BY_SESSION = """
DELETE FROM pc_trajectory_cell WHERE session_id = :session_id
"""

# idempotent column adds for the new cell detail/metric columns
# on existing DBs. CREATE TABLE IF NOT EXISTS does NOT add columns to an
# existing table.
# Each statement is a standalone ALTER TABLE ... ADD COLUMN IF NOT EXISTS
# so that a single missing column on a legacy DB does not roll back the
# rest of the migration (PostgreSQL treats multi-column ALTER ADD as a
# single atomic operation; any conflict aborts the whole batch).
ADD_CELL_DETAIL_COLUMNS: tuple[str, ...] = (
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS input_detail TEXT",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS output_detail TEXT",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS thinking_detail TEXT",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS model TEXT",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS finish_reason TEXT",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS usage_input_tokens INTEGER",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS usage_output_tokens INTEGER",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS usage_cache_read_tokens INTEGER",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS usage_cache_write_tokens INTEGER",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS usage_reasoning_tokens INTEGER",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS ttft_ms DOUBLE PRECISION",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS decoding_ms DOUBLE PRECISION",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS is_error BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS schema_detail TEXT",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS tool_name TEXT",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS tool_call_id TEXT",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS error TEXT",
    "ALTER TABLE pc_trajectory_cell ADD COLUMN IF NOT EXISTS iteration INTEGER",
)


# Full column list for INSERT statements (60 fields)
_RECORD_COLUMNS = """session_id, iteration, turn, started_at_ms, completed_at_ms, status,
    projector_input_order_count, projector_input_entry_types,
    projector_input_head_seq, projector_input_tail_seq, projector_input_tail_types,
    projector_msg_count, projector_msg_roles, projector_head, projector_tail,
    projector_appended_msg_count, projector_user_message_preview, projector_tool_results_preview,
    projector_meta,
    assemble_entry_msg_count, assemble_entry_roles, assemble_entry_head,
    assemble_entry_tail, assemble_entry_meta_keys, assemble_entry_meta_values_hash,
    pipeline_out_msg_count, pipeline_out_roles, pipeline_out_head, pipeline_out_tail,
    pipeline_out_budget_summary, pipeline_out_compressors_used, pipeline_out_compaction_happened,
    pipeline_out_compaction_summary,
    packer_final_msg_count, packer_final_roles, packer_final_head, packer_final_tail,
    packer_final_tokens, packer_final_model,
    packer_final_system_prompt_len, packer_final_system_prompt_sha1_16,
    packer_final_system_prompt_text, packer_final_system_prompt_budget,
    packer_final_tool_schema_total_chars, packer_final_response_format_text,
    packer_final_tool_schema_text,
    llm_provider, llm_model, finish_reason,
    usage_input_tokens, usage_output_tokens,
    usage_cache_read_tokens, usage_cache_write_tokens,
    usage_reasoning_tokens, ttft_ms, total_ms,
    assistant_text_preview, tool_calls_summary,
    error, error_stack"""

# Named placeholders matching the _record_to_params_dict keys
_RECORD_VALUES = """:session_id, :iteration, :turn, :started_at_ms, :completed_at_ms, :status,
    :projector_input_order_count, :projector_input_entry_types,
    :projector_input_head_seq, :projector_input_tail_seq, :projector_input_tail_types,
    :projector_msg_count, :projector_msg_roles, :projector_head, :projector_tail,
    :projector_appended_msg_count, :projector_user_message_preview, :projector_tool_results_preview,
    :projector_meta,
    :assemble_entry_msg_count, :assemble_entry_roles, :assemble_entry_head,
    :assemble_entry_tail, :assemble_entry_meta_keys, :assemble_entry_meta_values_hash,
    :pipeline_out_msg_count, :pipeline_out_roles, :pipeline_out_head, :pipeline_out_tail,
    :pipeline_out_budget_summary, :pipeline_out_compressors_used, :pipeline_out_compaction_happened,
    :pipeline_out_compaction_summary,
    :packer_final_msg_count, :packer_final_roles, :packer_final_head, :packer_final_tail,
    :packer_final_tokens, :packer_final_model,
    :packer_final_system_prompt_len, :packer_final_system_prompt_sha1_16,
    :packer_final_system_prompt_text, :packer_final_system_prompt_budget,
    :packer_final_tool_schema_total_chars, :packer_final_response_format_text,
    :packer_final_tool_schema_text,
    :llm_provider, :llm_model, :finish_reason,
    :usage_input_tokens, :usage_output_tokens,
    :usage_cache_read_tokens, :usage_cache_write_tokens,
    :usage_reasoning_tokens, :ttft_ms, :total_ms,
    :assistant_text_preview, :tool_calls_summary,
    :error, :error_stack"""

INSERT_RECORD = f"""
INSERT INTO pc_trajectory_record (
    {_RECORD_COLUMNS}
) VALUES (
    {_RECORD_VALUES}
)
RETURNING id
"""

# ON CONFLICT DO UPDATE SET — all updatable columns
_UPDATE_SET = """turn = EXCLUDED.turn,
    completed_at_ms = EXCLUDED.completed_at_ms,
    status = EXCLUDED.status,
    projector_input_order_count = EXCLUDED.projector_input_order_count,
    projector_input_entry_types = EXCLUDED.projector_input_entry_types,
    projector_input_head_seq = EXCLUDED.projector_input_head_seq,
    projector_input_tail_seq = EXCLUDED.projector_input_tail_seq,
    projector_input_tail_types = EXCLUDED.projector_input_tail_types,
    projector_msg_count = EXCLUDED.projector_msg_count,
    projector_msg_roles = EXCLUDED.projector_msg_roles,
    projector_head = EXCLUDED.projector_head,
    projector_tail = EXCLUDED.projector_tail,
    projector_appended_msg_count = EXCLUDED.projector_appended_msg_count,
    projector_user_message_preview = EXCLUDED.projector_user_message_preview,
    projector_tool_results_preview = EXCLUDED.projector_tool_results_preview,
    projector_meta = EXCLUDED.projector_meta,
    assemble_entry_msg_count = EXCLUDED.assemble_entry_msg_count,
    assemble_entry_roles = EXCLUDED.assemble_entry_roles,
    assemble_entry_head = EXCLUDED.assemble_entry_head,
    assemble_entry_tail = EXCLUDED.assemble_entry_tail,
    assemble_entry_meta_keys = EXCLUDED.assemble_entry_meta_keys,
    assemble_entry_meta_values_hash = EXCLUDED.assemble_entry_meta_values_hash,
    pipeline_out_msg_count = EXCLUDED.pipeline_out_msg_count,
    pipeline_out_roles = EXCLUDED.pipeline_out_roles,
    pipeline_out_head = EXCLUDED.pipeline_out_head,
    pipeline_out_tail = EXCLUDED.pipeline_out_tail,
    pipeline_out_budget_summary = EXCLUDED.pipeline_out_budget_summary,
    pipeline_out_compressors_used = EXCLUDED.pipeline_out_compressors_used,
    pipeline_out_compaction_happened = EXCLUDED.pipeline_out_compaction_happened,
    pipeline_out_compaction_summary = EXCLUDED.pipeline_out_compaction_summary,
    packer_final_msg_count = EXCLUDED.packer_final_msg_count,
    packer_final_roles = EXCLUDED.packer_final_roles,
    packer_final_head = EXCLUDED.packer_final_head,
    packer_final_tail = EXCLUDED.packer_final_tail,
    packer_final_tokens = EXCLUDED.packer_final_tokens,
    packer_final_model = EXCLUDED.packer_final_model,
    packer_final_system_prompt_len = EXCLUDED.packer_final_system_prompt_len,
    packer_final_system_prompt_sha1_16 = EXCLUDED.packer_final_system_prompt_sha1_16,
    packer_final_system_prompt_text = EXCLUDED.packer_final_system_prompt_text,
    packer_final_system_prompt_budget = EXCLUDED.packer_final_system_prompt_budget,
    packer_final_tool_schema_total_chars = EXCLUDED.packer_final_tool_schema_total_chars,
    packer_final_response_format_text = EXCLUDED.packer_final_response_format_text,
    packer_final_tool_schema_text = EXCLUDED.packer_final_tool_schema_text,
    llm_provider = EXCLUDED.llm_provider,
    llm_model = EXCLUDED.llm_model,
    finish_reason = EXCLUDED.finish_reason,
    usage_input_tokens = EXCLUDED.usage_input_tokens,
    usage_output_tokens = EXCLUDED.usage_output_tokens,
    usage_cache_read_tokens = EXCLUDED.usage_cache_read_tokens,
    usage_cache_write_tokens = EXCLUDED.usage_cache_write_tokens,
    usage_reasoning_tokens = EXCLUDED.usage_reasoning_tokens,
    ttft_ms = EXCLUDED.ttft_ms,
    total_ms = EXCLUDED.total_ms,
    assistant_text_preview = EXCLUDED.assistant_text_preview,
    tool_calls_summary = EXCLUDED.tool_calls_summary,
    error = EXCLUDED.error,
    error_stack = EXCLUDED.error_stack,
    updated_at = now()"""

INSERT_RECORD_UPSERT = f"""
INSERT INTO pc_trajectory_record (
    {_RECORD_COLUMNS}
) VALUES (
    {_RECORD_VALUES}
)
ON CONFLICT (session_id, iteration, started_at_ms) DO UPDATE SET
    {_UPDATE_SET}
RETURNING id
"""

SELECT_SESSION_META = """
SELECT * FROM pc_trajectory_session WHERE session_id = :session_id
"""

SELECT_RECORDS_BY_SESSION = """
SELECT * FROM pc_trajectory_record
WHERE session_id = :session_id
ORDER BY turn NULLS LAST, iteration
LIMIT :limit OFFSET :offset
"""

SELECT_RECORD_COUNT = """
SELECT COUNT(*) as cnt FROM pc_trajectory_record WHERE session_id = :session_id
"""

DELETE_RECORDS_BY_SESSION = """
DELETE FROM pc_trajectory_record WHERE session_id = :session_id
"""

DELETE_SESSION_META = """
DELETE FROM pc_trajectory_session WHERE session_id = :session_id
"""

# Incremental flush: get existing (iteration, started_at_ms) pairs for a session
SELECT_EXISTING_RECORD_KEYS = """
SELECT iteration, started_at_ms FROM pc_trajectory_record WHERE session_id = :session_id
"""

# Unique constraint for incremental upsert
ADD_UNIQUE_CONSTRAINT = """
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'uq_traj_record_session_iter_start'
  ) THEN
    ALTER TABLE pc_trajectory_record
    ADD CONSTRAINT uq_traj_record_session_iter_start
    UNIQUE (session_id, iteration, started_at_ms);
  END IF;
END $$;
"""

# Fix F: idempotent column add for existing DBs. CREATE TABLE IF NOT EXISTS
# does NOT add new columns to an existing table — so we need an explicit
# ALTER for upgrades. The CHECK on info_schema prevents "column exists"
# errors on re-runs.
ADD_PROJECTOR_APPENDED_MSG_COUNT_COLUMN = """
ALTER TABLE pc_trajectory_record
    ADD COLUMN IF NOT EXISTS projector_appended_msg_count INTEGER NOT NULL DEFAULT 0
"""

# idempotent column adds for the 6 new content-preview fields.
# Each ALTER only fires if the column doesn't exist yet — safe to re-run.
ADD_CONTENT_PREVIEW_COLUMNS = """
ALTER TABLE pc_trajectory_record
    ADD COLUMN IF NOT EXISTS projector_user_message_preview TEXT,
    ADD COLUMN IF NOT EXISTS projector_tool_results_preview JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS pipeline_out_compaction_summary TEXT,
    ADD COLUMN IF NOT EXISTS packer_final_tool_schema_text TEXT,
    ADD COLUMN IF NOT EXISTS assistant_text_preview TEXT,
    ADD COLUMN IF NOT EXISTS tool_calls_summary JSONB NOT NULL DEFAULT '[]'
"""

__all__ = [
    "CREATE_TRAJECTORY_SESSION_TABLE",
    "CREATE_TRAJECTORY_RECORD_TABLE",
    "CREATE_TRAJECTORY_CELL_TABLE",
    "CREATE_SCHEMA_MIGRATIONS_TABLE",
    "CHECK_MIGRATION",
    "RECORD_MIGRATION",
    "INDEXES",
    "INDEX_TRAJECTORY_CELL_SESSION",
    "INDEX_TRAJECTORY_CELL_TURN_ITER",
    "TOUCH_UPDATED_AT",
    "UPSERT_SESSION_META",
    "INSERT_RECORD",
    "INSERT_RECORD_UPSERT",
    "INSERT_CELL",
    "MARK_CELL_ERROR",
    "INSERT_ERROR_CELL",
    "INSERT_SYSTEM_CELL",
    "SELECT_SESSION_META",
    "SELECT_RECORDS_BY_SESSION",
    "SELECT_CELLS_BY_SESSION",
    "SELECT_CELLS_INCREMENTAL",
    "SELECT_RECORD_COUNT",
    "SELECT_EXISTING_RECORD_KEYS",
    "DELETE_RECORDS_BY_SESSION",
    "DELETE_CELLS_BY_SESSION",
    "DELETE_SESSION_META",
    "ADD_UNIQUE_CONSTRAINT",
    "ADD_PROJECTOR_APPENDED_MSG_COUNT_COLUMN",
    "ADD_CONTENT_PREVIEW_COLUMNS",
    "ADD_CELL_DETAIL_COLUMNS",
]
