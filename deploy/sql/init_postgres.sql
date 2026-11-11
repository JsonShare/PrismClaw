-- PrismClaw PostgreSQL bootstrap DDL.
--
-- Mounted by docker-compose to /docker-entrypoint-initdb.d/init_postgres.sql
-- so the VectorChord Suite image runs it once on first database creation.
-- Every statement is idempotent (IF NOT EXISTS / OR REPLACE / DROP IF EXISTS)
-- so running this file repeatedly (e.g. re-init after schema reset) is safe.
--
-- Sections:
--   1. Extensions  (vchord, pg_tokenizer, vchord_bm25, vector)
--   2. Web channel user tables  (pc_users + pc_users_config)
--   3. Context retrieval audit  (pc_retrieval_audit_entries)
--
-- NOTE: the application also runs its own init_schema() at startup for the
-- remaining 11 pc_* tables (memory, scheduler, dream, run_snapshots). This
-- file covers ONLY the pieces that must be present before the app first
-- connects (extensions) plus the DDL source-of-truth reference tables that
-- DBAs commonly want inspectable as standalone SQL files.

-- ─────────────────────────────────────────────────────────────────────
-- 1. VectorChord Suite extensions
--    Installed once at container init. CASCADE pulls in implicit deps.
--    vchord_bm25  : BM25 keyword ranking via the `@@@` operator (replaces
--                   the broken `to_tsvector('simple')` path which cannot
--                   segment CJK text without a tokenizer).
--    pg_tokenizer : CJK-aware tokenizer that vchord_bm25 indexes consume.
--    vchord       : high-performance vector similarity search (Rust).
--    vector       : pgvector — embedding type + `<=>` distance.
-- ─────────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vchord      CASCADE;
CREATE EXTENSION IF NOT EXISTS pg_tokenizer CASCADE;
CREATE EXTENSION IF NOT EXISTS vchord_bm25 CASCADE;
CREATE EXTENSION IF NOT EXISTS vector      CASCADE;

-- ─────────────────────────────────────────────────────────────────────
-- 2. Web channel user table (mirrors auth/userdb/models.py)
--    pc_users : core authentication (credentials, lockout).
--    Timestamps are stored WITHOUT time zone; always interpret as UTC.
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pc_users (
    id                  TEXT        PRIMARY KEY,
    password_hash       TEXT        NOT NULL,
    email               TEXT,                       -- 登录/找回密码邮箱（可空）
    nickname            TEXT,                       -- 展示与登录昵称（非空、忽略大小写唯一）
    disabled            BOOLEAN     NOT NULL DEFAULT FALSE,
    failed_attempts     INTEGER     NOT NULL DEFAULT 0,
    locked_until        TIMESTAMP,                  -- UTC
    last_login_at       TIMESTAMP,                  -- UTC
    password_changed_at TIMESTAMP,                  -- UTC
    created_at          TIMESTAMP   NOT NULL DEFAULT now(),
    updated_at          TIMESTAMP   NOT NULL DEFAULT now()
);

-- 历史遗留：role 列与 pc_users_config 表（本地助手时代的 admin/member 权限
-- 模型）已废弃。应用启动时 init_schema() 会对存量库执行幂等迁移
-- （_migrate_drop_role_and_config）清理这两处残留。

CREATE INDEX IF NOT EXISTS idx_pc_users_disabled    ON pc_users (disabled);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pc_users_email_lower
    ON pc_users (lower(email)) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_pc_users_nickname_lower
    ON pc_users (lower(nickname)) WHERE nickname IS NOT NULL;

-- Keep ``updated_at`` fresh on every row UPDATE (zero app-code cost).
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pc_users_touch          ON pc_users;
CREATE TRIGGER trg_pc_users_touch
    BEFORE UPDATE ON pc_users
    FOR EACH ROW
    EXECUTE FUNCTION touch_updated_at();

-- ─────────────────────────────────────────────────────────────────────
-- 3. Context retrieval audit log
--    Mirrors core/subsystems/context/retrieval_audit.py
--    PostgresRetrievalAuditLog._init_schema().
--    The 4 trailing boolean columns (memory_vector_fallback,
--    archive_vector_fallback, l2l3_error, archive_error) are required
--    by RetrievalAuditEntry.record().
-- ─────────────────────────────────────────────────────────────────────

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

-- DEF-01: 工具调用审计表
CREATE TABLE IF NOT EXISTS pc_tool_call_audit (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    turn_seq        INTEGER NOT NULL,
    iteration_seq   INTEGER NOT NULL,
    tool_name       TEXT NOT NULL,
    args_hash       TEXT NOT NULL,
    args_sanitized  TEXT NOT NULL DEFAULT '',
    args_preview    TEXT NOT NULL DEFAULT '',
    sandbox_tier    TEXT NOT NULL,
    confirmation_kind TEXT NOT NULL DEFAULT 'NONE',
    risk_assessment_json TEXT NOT NULL DEFAULT '{}',
    workspace_tag_kind  TEXT NOT NULL DEFAULT 'GENERAL',
    consent_purposes    JSONB NOT NULL DEFAULT '[]',
    skill_name      TEXT,
    plugin_name     TEXT,
    subagent_run_id TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    exit_code       INTEGER,
    elapsed_ms      INTEGER,
    error_summary   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    tenant_id       TEXT
);

CREATE INDEX idx_tca_session ON pc_tool_call_audit (session_id);
CREATE INDEX idx_tca_tool_time ON pc_tool_call_audit (tool_name, created_at DESC);
CREATE INDEX idx_tca_workspace_tag ON pc_tool_call_audit (workspace_tag_kind, created_at DESC);
CREATE INDEX idx_tca_tenant ON pc_tool_call_audit (tenant_id, created_at DESC) WHERE tenant_id IS NOT NULL;
CREATE INDEX idx_tca_session_tool ON pc_tool_call_audit (session_id, tool_name, created_at DESC);

-- DEF-01: 审批审计表
CREATE TABLE IF NOT EXISTS pc_approval_audit (
    id                  TEXT PRIMARY KEY,
    tool_call_id        TEXT NOT NULL REFERENCES pc_tool_call_audit(id),
    decision            TEXT NOT NULL,
    confirm_level       TEXT NOT NULL,
    requester_id        TEXT NOT NULL,
    responder_id        TEXT,
    second_approver_id  TEXT,
    signed_payload      TEXT NOT NULL,
    verified            BOOLEAN NOT NULL DEFAULT FALSE,
    batch_size          INTEGER NOT NULL DEFAULT 1,
    batch_tool_call_ids JSONB NOT NULL DEFAULT '[]',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ,
    tenant_id           TEXT
);

CREATE INDEX idx_aa_tool_call ON pc_approval_audit (tool_call_id);
CREATE INDEX idx_aa_responder ON pc_approval_audit (responder_id, created_at DESC);
CREATE INDEX idx_aa_tenant ON pc_approval_audit (tenant_id, created_at DESC) WHERE tenant_id IS NOT NULL;

-- DEF-03: 租户策略表
CREATE TABLE IF NOT EXISTS pc_tenant_policies (
    tenant_id TEXT PRIMARY KEY,
    policy_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT NOT NULL
);
