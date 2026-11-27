-- PrismClaw PostgreSQL bootstrap DDL.
--
-- 容器首次初始化与应用启动都会执行本文件；所有语句幂等。
-- 应用侧会跳过 CREATE EXTENSION（扩展由容器初始化或 DBA 预装）。

CREATE EXTENSION IF NOT EXISTS vchord       CASCADE;
CREATE EXTENSION IF NOT EXISTS pg_tokenizer CASCADE;
CREATE EXTENSION IF NOT EXISTS vchord_bm25  CASCADE;
CREATE EXTENSION IF NOT EXISTS vector       CASCADE;

-- ── 用户 ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pc_users (
    id                  TEXT        PRIMARY KEY,
    password_hash       TEXT        NOT NULL,
    email               TEXT,
    nickname            TEXT,
    disabled            BOOLEAN     NOT NULL DEFAULT FALSE,
    failed_attempts     INTEGER     NOT NULL DEFAULT 0,
    locked_until        TIMESTAMP,
    last_login_at       TIMESTAMP,
    password_changed_at TIMESTAMP,
    created_at          TIMESTAMP   NOT NULL DEFAULT now(),
    updated_at          TIMESTAMP   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pc_users_disabled ON pc_users (disabled);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pc_users_email_lower
    ON pc_users (lower(email)) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_pc_users_nickname_lower
    ON pc_users (lower(nickname)) WHERE nickname IS NOT NULL;

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pc_users_touch ON pc_users;
CREATE TRIGGER trg_pc_users_touch
    BEFORE UPDATE ON pc_users
    FOR EACH ROW
    EXECUTE FUNCTION touch_updated_at();

-- ── 检索审计 ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pc_retrieval_audit_entries (
    id                                  BIGSERIAL PRIMARY KEY,
    session_key                         TEXT NOT NULL,
    retrieval_query                     TEXT NOT NULL,
    started_at                          DOUBLE PRECISION NOT NULL,
    ended_at                            DOUBLE PRECISION NOT NULL,
    duration_s                          DOUBLE PRECISION NOT NULL,
    memory_candidate_count              INTEGER NOT NULL DEFAULT 0,
    archive_candidate_count             INTEGER NOT NULL DEFAULT 0,
    memory_text_candidate_count         INTEGER NOT NULL DEFAULT 0,
    memory_vector_candidate_count       INTEGER NOT NULL DEFAULT 0,
    archive_text_candidate_count        INTEGER NOT NULL DEFAULT 0,
    archive_vector_candidate_count      INTEGER NOT NULL DEFAULT 0,
    selected_memory_count               INTEGER NOT NULL DEFAULT 0,
    selected_archive_count              INTEGER NOT NULL DEFAULT 0,
    selected_layer_distribution_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    memory_source_mix_json              JSONB NOT NULL DEFAULT '{}'::jsonb,
    archive_source_mix_json             JSONB NOT NULL DEFAULT '{}'::jsonb,
    selector_strategy                   TEXT NOT NULL DEFAULT 'unknown',
    selector_fallback                   BOOLEAN NOT NULL DEFAULT FALSE,
    planner_strategy                    TEXT NOT NULL DEFAULT 'unknown',
    planner_temporal_mode               TEXT NOT NULL DEFAULT 'unknown',
    planner_memory_layers_json          JSONB NOT NULL DEFAULT '[]'::jsonb,
    planner_artifact_kinds_json         JSONB NOT NULL DEFAULT '[]'::jsonb,
    planner_graph_expansion_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    archive_cache_hit                   BOOLEAN NOT NULL DEFAULT FALSE,
    memory_vector_fallback              BOOLEAN NOT NULL DEFAULT FALSE,
    archive_vector_fallback             BOOLEAN NOT NULL DEFAULT FALSE,
    l2l3_error                          BOOLEAN NOT NULL DEFAULT FALSE,
    archive_error                       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_pc_retrieval_audit_session
    ON pc_retrieval_audit_entries (session_key, started_at DESC);

-- ── 轨迹 ────────────────────────────────────────────────────────────

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
);

CREATE TABLE IF NOT EXISTS pc_trajectory_record (
    id                      BIGSERIAL   PRIMARY KEY,
    session_id              TEXT        NOT NULL,
    iteration               INTEGER     NOT NULL,
    turn                    INTEGER,
    started_at_ms           BIGINT      NOT NULL,
    completed_at_ms         BIGINT,
    status                  TEXT        NOT NULL DEFAULT 'running'
                                        CHECK (status IN ('running', 'ok', 'error', 'aborted', 'timeout', 'orphaned')),
    projector_input_order_count INTEGER  NOT NULL DEFAULT 0,
    projector_input_entry_types JSONB    NOT NULL DEFAULT '[]',
    projector_input_head_seq    JSONB    NOT NULL DEFAULT '[]',
    projector_input_tail_seq    JSONB    NOT NULL DEFAULT '[]',
    projector_input_tail_types  JSONB    NOT NULL DEFAULT '[]',
    projector_msg_count     INTEGER     NOT NULL DEFAULT 0,
    projector_msg_roles     JSONB       NOT NULL DEFAULT '[]',
    projector_head          JSONB       NOT NULL DEFAULT '[]',
    projector_tail          JSONB       NOT NULL DEFAULT '[]',
    projector_appended_msg_count INTEGER NOT NULL DEFAULT 0,
    projector_user_message_preview TEXT,
    projector_tool_results_preview JSONB NOT NULL DEFAULT '[]',
    projector_meta          JSONB       NOT NULL DEFAULT '{}',
    assemble_entry_msg_count INTEGER    NOT NULL DEFAULT 0,
    assemble_entry_roles    JSONB       NOT NULL DEFAULT '[]',
    assemble_entry_head     JSONB       NOT NULL DEFAULT '[]',
    assemble_entry_tail     JSONB       NOT NULL DEFAULT '[]',
    assemble_entry_meta_keys JSONB      NOT NULL DEFAULT '[]',
    assemble_entry_meta_values_hash TEXT NOT NULL DEFAULT '',
    pipeline_out_msg_count  INTEGER     NOT NULL DEFAULT 0,
    pipeline_out_roles      JSONB       NOT NULL DEFAULT '[]',
    pipeline_out_head       JSONB       NOT NULL DEFAULT '[]',
    pipeline_out_tail       JSONB       NOT NULL DEFAULT '[]',
    pipeline_out_budget_summary JSONB   NOT NULL DEFAULT '{}',
    pipeline_out_compressors_used JSONB NOT NULL DEFAULT '[]',
    pipeline_out_compaction_happened BOOLEAN NOT NULL DEFAULT FALSE,
    pipeline_out_compaction_summary TEXT,
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
    error                   TEXT,
    error_stack             TEXT,
    created_at              TIMESTAMP   NOT NULL DEFAULT now(),
    updated_at              TIMESTAMP   NOT NULL DEFAULT now(),
    CONSTRAINT uq_traj_record_session_iter_start
        UNIQUE (session_id, iteration, started_at_ms),
    CONSTRAINT fk_pc_trajectory_record_session
        FOREIGN KEY (session_id) REFERENCES pc_trajectory_session(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pc_trajectory_cell (
    cell_id          TEXT        NOT NULL,
    session_id      TEXT        NOT NULL,
    entry_id        TEXT        NOT NULL,
    seq              INTEGER     NOT NULL,
    turn             INTEGER     NOT NULL,
    kind             TEXT        NOT NULL,
    role             TEXT        NOT NULL,
    content_preview  TEXT,
    tool_calls_summary JSONB    NOT NULL DEFAULT '[]',
    tool_call_id     TEXT,
    tool_name        TEXT,
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

CREATE INDEX IF NOT EXISTS idx_traj_record_session_turn
    ON pc_trajectory_record (session_id, turn, iteration);
CREATE INDEX IF NOT EXISTS idx_traj_record_status
    ON pc_trajectory_record (status);
CREATE INDEX IF NOT EXISTS idx_traj_record_started_at
    ON pc_trajectory_record (started_at_ms);
CREATE INDEX IF NOT EXISTS idx_traj_record_projector_entry_types
    ON pc_trajectory_record USING GIN (projector_input_entry_types);
CREATE INDEX IF NOT EXISTS idx_trajectory_cell_session
    ON pc_trajectory_cell (session_id, seq);
CREATE INDEX IF NOT EXISTS idx_trajectory_cell_turn_iter
    ON pc_trajectory_cell (session_id, turn, iteration, kind);

CREATE OR REPLACE FUNCTION pc_traj_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_traj_session_touch ON pc_trajectory_session;
CREATE TRIGGER trg_traj_session_touch
    BEFORE UPDATE ON pc_trajectory_session
    FOR EACH ROW EXECUTE FUNCTION pc_traj_touch_updated_at();

DROP TRIGGER IF EXISTS trg_traj_record_touch ON pc_trajectory_record;
CREATE TRIGGER trg_traj_record_touch
    BEFORE UPDATE ON pc_trajectory_record
    FOR EACH ROW EXECUTE FUNCTION pc_traj_touch_updated_at();

-- ── 定时任务 ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pc_scheduled_tasks (
    id                   VARCHAR PRIMARY KEY,
    user_id              VARCHAR NOT NULL,
    workspace_id         VARCHAR NOT NULL DEFAULT 'default',
    name                 VARCHAR NOT NULL,
    instruction          TEXT NOT NULL,
    cron_expr            VARCHAR NOT NULL,
    schedule_spec        TEXT NOT NULL,
    enabled              BOOLEAN NOT NULL DEFAULT true,
    conversation_id      VARCHAR,
    last_fired_at        DOUBLE PRECISION,
    last_status          VARCHAR NOT NULL DEFAULT 'idle',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    created_at           DOUBLE PRECISION NOT NULL,
    max_age_days         INTEGER NOT NULL DEFAULT 30,
    valid_from           DOUBLE PRECISION,
    valid_until          DOUBLE PRECISION,
    missed_policy        VARCHAR NOT NULL DEFAULT 'skip',
    CONSTRAINT ck_pc_sched_tasks_last_status
        CHECK (last_status IN ('idle','running','success','failed','disabled')),
    CONSTRAINT ck_pc_sched_tasks_missed_policy
        CHECK (missed_policy IN ('skip','run_once'))
);

CREATE INDEX IF NOT EXISTS idx_pc_sched_tasks_user_workspace
    ON pc_scheduled_tasks (user_id, workspace_id);

-- 存量库升级：有效期区间（CREATE TABLE IF NOT EXISTS 不会补列）
ALTER TABLE pc_scheduled_tasks
    ADD COLUMN IF NOT EXISTS valid_from DOUBLE PRECISION;
ALTER TABLE pc_scheduled_tasks
    ADD COLUMN IF NOT EXISTS valid_until DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_pc_sched_tasks_enabled
    ON pc_scheduled_tasks (enabled) WHERE enabled = true;

CREATE TABLE IF NOT EXISTS pc_scheduled_task_runs (
    id          VARCHAR PRIMARY KEY,
    task_id     VARCHAR NOT NULL,
    fired_at    DOUBLE PRECISION NOT NULL,
    started_at  DOUBLE PRECISION,
    finished_at DOUBLE PRECISION,
    status      VARCHAR NOT NULL DEFAULT 'pending',
    session_id  VARCHAR,
    error       TEXT,
    duration_s  DOUBLE PRECISION,
    CONSTRAINT ck_pc_sched_runs_status
        CHECK (status IN ('pending','running','success','failed'))
);

CREATE INDEX IF NOT EXISTS idx_pc_sched_runs_task
    ON pc_scheduled_task_runs (task_id, fired_at);

-- ── Dream ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pc_dream_states (
    session_key                       TEXT PRIMARY KEY,
    dream_pending                     BOOLEAN NOT NULL DEFAULT FALSE,
    memory_dirty_score                INTEGER NOT NULL DEFAULT 0,
    last_activity_at                  DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_light_at                     DOUBLE PRECISION,
    last_deep_at                      DOUBLE PRECISION,
    last_rem_at                       DOUBLE PRECISION,
    dream_batch_id                    TEXT,
    dream_lock_owner                  TEXT,
    light_pending                     BOOLEAN NOT NULL DEFAULT FALSE,
    nightly_pending                   BOOLEAN NOT NULL DEFAULT FALSE,
    last_nightly_window_started_at    DOUBLE PRECISION,
    compile_cursor                    TEXT,
    last_compile_started_at           DOUBLE PRECISION,
    last_compile_finished_at          DOUBLE PRECISION,
    publish_watermark_at              DOUBLE PRECISION,
    last_compiled_candidate_count     INTEGER NOT NULL DEFAULT 0,
    last_published_memory_count       INTEGER NOT NULL DEFAULT 0,
    last_gate_rejected_entries        INTEGER NOT NULL DEFAULT 0,
    last_compile_status               TEXT,
    last_enqueue_activity_at          DOUBLE PRECISION,
    last_enqueue_activity_status      TEXT,
    last_enqueue_activity_detail      TEXT,
    last_enqueue_nightly_at           DOUBLE PRECISION,
    last_enqueue_nightly_status       TEXT,
    last_enqueue_nightly_detail       TEXT
);

CREATE TABLE IF NOT EXISTS pc_dream_candidates (
    candidate_id           TEXT PRIMARY KEY,
    session_key            TEXT NOT NULL,
    session_id             TEXT NOT NULL,
    kind                   TEXT NOT NULL,
    content                TEXT NOT NULL,
    source_entry_ids_json  JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at             DOUBLE PRECISION NOT NULL,
    updated_at             DOUBLE PRECISION NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'draft',
    metadata_json          TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_pc_dream_candidates_session
    ON pc_dream_candidates(session_key, status, kind, created_at DESC);

CREATE TABLE IF NOT EXISTS pc_dream_audit_entries (
    id                       BIGSERIAL PRIMARY KEY,
    session_key              TEXT NOT NULL,
    batch_id                 TEXT NOT NULL,
    phase                    TEXT NOT NULL,
    trigger                  TEXT NOT NULL,
    status                   TEXT NOT NULL,
    started_at               DOUBLE PRECISION NOT NULL,
    ended_at                 DOUBLE PRECISION NOT NULL,
    duration_s               DOUBLE PRECISION NOT NULL,
    promoted_count           INTEGER NOT NULL DEFAULT 0,
    deduplicated_count       INTEGER NOT NULL DEFAULT 0,
    archived_count           INTEGER NOT NULL DEFAULT 0,
    synthesized_count        INTEGER NOT NULL DEFAULT 0,
    compiled_candidates      INTEGER NOT NULL DEFAULT 0,
    extracted_candidates     INTEGER NOT NULL DEFAULT 0,
    published_candidates     INTEGER NOT NULL DEFAULT 0,
    discarded_candidates     INTEGER NOT NULL DEFAULT 0,
    merged_entries           INTEGER NOT NULL DEFAULT 0,
    temporal_updates         INTEGER NOT NULL DEFAULT 0,
    gate_rejected_entries    INTEGER NOT NULL DEFAULT 0,
    published_memory_count   INTEGER NOT NULL DEFAULT 0,
    llm_cost                 INTEGER NOT NULL DEFAULT 0,
    failure_reason           TEXT
);

CREATE INDEX IF NOT EXISTS idx_pc_dream_audit_batch
    ON pc_dream_audit_entries(batch_id);
CREATE INDEX IF NOT EXISTS idx_pc_dream_audit_session
    ON pc_dream_audit_entries(session_key);

CREATE TABLE IF NOT EXISTS pc_dream_decision_audit (
    decision_id        TEXT PRIMARY KEY,
    batch_id           TEXT NOT NULL,
    session_key        TEXT NOT NULL,
    record_id          TEXT NOT NULL,
    target_id          TEXT NOT NULL,
    action             TEXT NOT NULL,
    reason             TEXT NOT NULL,
    health_status      TEXT NOT NULL,
    rollout_percentage INTEGER NOT NULL DEFAULT 0,
    trigger            TEXT NOT NULL,
    dry_run            BOOLEAN NOT NULL DEFAULT FALSE,
    applied            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS pc_dream_governance_records (
    record_id        TEXT PRIMARY KEY,
    session_key      TEXT NOT NULL,
    target_type      TEXT NOT NULL,
    target_id        TEXT NOT NULL,
    publish_state    TEXT NOT NULL,
    review_status    TEXT NOT NULL,
    publish_channel  TEXT NOT NULL DEFAULT 'direct',
    rollout_stage    TEXT NOT NULL DEFAULT 'live',
    shadow           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       DOUBLE PRECISION NOT NULL,
    updated_at       DOUBLE PRECISION NOT NULL,
    metadata_json    JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_pc_dream_governance_lookup
    ON pc_dream_governance_records(session_key, target_type, review_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pc_dream_governance_target_lookup
    ON pc_dream_governance_records(session_key, target_type, target_id, created_at DESC);

-- ── 运行快照 ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pc_run_snapshots (
    run_id          VARCHAR PRIMARY KEY,
    session_id      VARCHAR NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      DOUBLE PRECISION NOT NULL,
    updated_at      DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pc_run_snapshots_session
    ON pc_run_snapshots (session_id, updated_at DESC);

-- ── 记忆（向量维度固定为 1024） ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS pc_memory_entries (
    id VARCHAR PRIMARY KEY,
    session_key VARCHAR NOT NULL,
    layer VARCHAR NOT NULL,
    type VARCHAR NOT NULL,
    content TEXT NOT NULL,
    scope VARCHAR,
    entity_key VARCHAR,
    source_session_id VARCHAR,
    source_refs_json JSONB DEFAULT '[]'::jsonb,
    created_at FLOAT NOT NULL,
    updated_at FLOAT NOT NULL,
    last_used_at FLOAT,
    use_count INTEGER DEFAULT 0,
    status VARCHAR DEFAULT 'active',
    origin_stage VARCHAR,
    confidence FLOAT,
    stability_score FLOAT,
    cluster_id VARCHAR,
    supersedes_json JSONB DEFAULT '[]'::jsonb,
    last_validated_at FLOAT,
    graph_group_id VARCHAR,
    version_chain_id VARCHAR,
    publish_state VARCHAR,
    valid_from FLOAT,
    valid_to FLOAT,
    expires_at FLOAT,
    winner_flag BOOLEAN DEFAULT TRUE,
    low_confidence BOOLEAN DEFAULT FALSE,
    content_hash VARCHAR,
    embedding VECTOR(1024),
    content_bm25 bm25vector
);

CREATE INDEX IF NOT EXISTS idx_pc_memory_session_key
    ON pc_memory_entries (session_key);
CREATE INDEX IF NOT EXISTS idx_pc_memory_embedding_hnsw
    ON pc_memory_entries USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS pc_memory_l4_artifacts (
    id VARCHAR PRIMARY KEY,
    session_key VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    artifact_kind VARCHAR NOT NULL,
    summary TEXT NOT NULL,
    created_at FLOAT NOT NULL,
    updated_at FLOAT NOT NULL,
    graph_group_id VARCHAR,
    source_refs_json JSONB DEFAULT '[]'::jsonb,
    origin_stage VARCHAR,
    publish_state VARCHAR,
    payload_json JSONB DEFAULT '{}'::jsonb,
    low_confidence BOOLEAN DEFAULT FALSE,
    embedding VECTOR(1024),
    summary_bm25 bm25vector
);

CREATE INDEX IF NOT EXISTS idx_pc_l4_session_key
    ON pc_memory_l4_artifacts (session_key);
CREATE INDEX IF NOT EXISTS idx_pc_l4_artifact_kind
    ON pc_memory_l4_artifacts (artifact_kind);
CREATE INDEX IF NOT EXISTS idx_pc_l4_embedding_hnsw
    ON pc_memory_l4_artifacts USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS pc_memory_reference_log (
    id VARCHAR PRIMARY KEY,
    session_key VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    turn_seq INTEGER,
    entry_id VARCHAR NOT NULL,
    query_text TEXT,
    query_embedding VECTOR(1024),
    relevance_score FLOAT,
    injected_at FLOAT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pmrl_entry ON pc_memory_reference_log (entry_id);
CREATE INDEX IF NOT EXISTS idx_pmrl_session ON pc_memory_reference_log (session_id);

CREATE TABLE IF NOT EXISTS pc_memory_validation_events (
    id VARCHAR PRIMARY KEY,
    session_key VARCHAR NOT NULL,
    entry_id VARCHAR NOT NULL,
    signal_type VARCHAR NOT NULL,
    polarity VARCHAR NOT NULL,
    attribution VARCHAR NOT NULL,
    source_ref VARCHAR,
    occurred_at FLOAT NOT NULL,
    payload_json JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_pmve_entry ON pc_memory_validation_events (entry_id);

-- 仲裁审计同时服务 shadow 回放与决策门禁；executed 区分已决策未执行和已执行。
CREATE TABLE IF NOT EXISTS pc_memory_arbitration_audit (
    id VARCHAR PRIMARY KEY,
    session_key VARCHAR NOT NULL,
    candidate_json JSONB NOT NULL,
    near_neighbors JSONB,
    decision VARCHAR NOT NULL,
    decided_by VARCHAR NOT NULL,
    mode VARCHAR NOT NULL,
    executed BOOLEAN DEFAULT FALSE,
    target_id VARCHAR,
    reason TEXT,
    latency_ms INTEGER,
    created_at FLOAT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pmaa_created ON pc_memory_arbitration_audit (created_at);

CREATE INDEX IF NOT EXISTS idx_entries_entity_key
    ON pc_memory_entries (session_key, entity_key)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS pc_memory_edges (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    session_key TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    predicate TEXT NOT NULL
        CHECK (predicate IN ('causes', 'prohibits', 'is_a', 'precedes', 'replaces', 'conflicts')),
    condition TEXT NOT NULL DEFAULT '',
    confidence REAL DEFAULT 1.0,
    evidence TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive')),
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION
);

-- 部分唯一索引只约束 active 边，允许同一逻辑边经历 active → inactive → active。
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique
    ON pc_memory_edges (session_key, source_id, target_id, predicate, condition)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_edges_src
    ON pc_memory_edges (session_key, source_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_edges_tgt
    ON pc_memory_edges (session_key, target_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_edges_pred
    ON pc_memory_edges (session_key, predicate) WHERE status = 'active';

-- jieba 预分词器，供两组 BM25 模型共用。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM tokenizer_catalog.text_analyzer WHERE name = 'jieba_analyzer'
    ) THEN
        PERFORM create_text_analyzer('jieba_analyzer', '[pre_tokenizer.jieba]');
    END IF;
END $$;

-- 模型、tokenizer 与触发器由扩展函数一并创建，写入时自动填充 bm25vector。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM tokenizer_catalog.model WHERE name = 'mem_content_model'
    ) THEN
        PERFORM create_custom_model_tokenizer_and_trigger(
            tokenizer_name => 'mem_content_tok',
            model_name => 'mem_content_model',
            text_analyzer_name => 'jieba_analyzer',
            table_name => 'pc_memory_entries',
            source_column => 'content',
            target_column => 'content_bm25'
        );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM tokenizer_catalog.model WHERE name = 'l4_summary_model'
    ) THEN
        PERFORM create_custom_model_tokenizer_and_trigger(
            tokenizer_name => 'l4_summary_tok',
            model_name => 'l4_summary_model',
            text_analyzer_name => 'jieba_analyzer',
            table_name => 'pc_memory_l4_artifacts',
            source_column => 'summary',
            target_column => 'summary_bm25'
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_pc_memory_content_bm25
    ON pc_memory_entries USING bm25 (content_bm25 bm25_ops);
CREATE INDEX IF NOT EXISTS idx_pc_l4_summary_bm25
    ON pc_memory_l4_artifacts USING bm25 (summary_bm25 bm25_ops);
