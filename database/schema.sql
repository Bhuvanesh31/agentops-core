-- ============================================================
-- AgentOps Core
-- Initial PostgreSQL schema
--
-- PostgreSQL is the system of record.
-- Git is used for provenance and fallback capture.
-- Qdrant, if added later, remains a rebuildable search index.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ============================================================
-- PROJECTS
-- Represents a business or software project.
-- One project may contain multiple repositories.
-- ============================================================

CREATE TABLE projects (
    project_id TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    owner TEXT,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- REPOSITORIES
-- Represents a Git repository belonging to a project.
-- ============================================================

CREATE TABLE repositories (
    repository_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL
        REFERENCES projects(project_id)
        ON DELETE CASCADE,

    repository_name TEXT NOT NULL,
    remote_url TEXT,
    local_path TEXT,
    default_branch TEXT NOT NULL DEFAULT 'main',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- TOOLS
-- Represents Claude Code, Codex, or another AI development tool.
-- ============================================================

CREATE TABLE tools (
    tool_id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    provider TEXT,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- RUNS
-- Represents one AI-assisted work session.
--
-- A run belongs to:
-- - one project
-- - one repository
-- - one tool
--
-- The tool-native session_id is used for reconciliation.
-- ============================================================

CREATE TABLE runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    project_id TEXT NOT NULL
        REFERENCES projects(project_id)
        ON DELETE CASCADE,

    repository_id TEXT NOT NULL
        REFERENCES repositories(repository_id)
        ON DELETE CASCADE,

    tool_id TEXT NOT NULL
        REFERENCES tools(tool_id),

    tool_version TEXT,
    model TEXT,
    session_id TEXT,
    human TEXT,
    machine_name TEXT,

    branch TEXT,
    worktree TEXT,
    cwd TEXT,

    task TEXT,
    intent TEXT,
    summary TEXT,

    status TEXT NOT NULL DEFAULT 'active',
    error_message TEXT,

    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT runs_status_check
        CHECK (
            status IN (
                'active',
                'completed',
                'committed',
                'merged',
                'rejected',
                'abandoned',
                'failed'
            )
        ),

    CONSTRAINT runs_tool_repository_session_unique
        UNIQUE (tool_id, repository_id, session_id)
);


-- ============================================================
-- RUN EVENTS
-- Raw events emitted by Claude Code, Codex, Git, or future tools.
--
-- These events form the append-only audit trail.
-- Consumers should normally read normalized runs rather than
-- querying raw payloads directly.
-- ============================================================

CREATE TABLE run_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    source_event_id TEXT,

    run_id UUID
        REFERENCES runs(run_id)
        ON DELETE CASCADE,

    tool_id TEXT
        REFERENCES tools(tool_id),

    session_id TEXT,
    event_type TEXT NOT NULL,
    tool_name TEXT,

    files_touched TEXT[] NOT NULL DEFAULT '{}',
    raw_payload JSONB NOT NULL DEFAULT '{}',

    redaction_status TEXT NOT NULL DEFAULT 'not_checked',
    ingestion_status TEXT NOT NULL DEFAULT 'received',

    occurred_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT run_events_redaction_status_check
        CHECK (
            redaction_status IN (
                'not_checked',
                'clean',
                'redacted',
                'blocked',
                'failed'
            )
        ),

    CONSTRAINT run_events_ingestion_status_check
        CHECK (
            ingestion_status IN (
                'received',
                'processed',
                'duplicate',
                'failed'
            )
        ),

    CONSTRAINT run_events_source_event_unique
        UNIQUE (source_event_id)
);


-- ============================================================
-- COMMITS
-- Connects Git commits to AgentOps runs.
--
-- run_id may be null initially when a Git event arrives before
-- the matching AI run has been identified.
-- ============================================================

CREATE TABLE commits (
    commit_sha TEXT PRIMARY KEY,

    run_id UUID
        REFERENCES runs(run_id)
        ON DELETE SET NULL,

    repository_id TEXT NOT NULL
        REFERENCES repositories(repository_id)
        ON DELETE CASCADE,

    tool_id TEXT
        REFERENCES tools(tool_id),

    session_id TEXT,

    branch TEXT,
    author_name TEXT,
    author_email TEXT,
    commit_message TEXT,

    committed_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- OUTCOMES
-- Human and system evaluation of whether a run was useful.
-- One run has at most one current outcome record.
-- ============================================================

CREATE TABLE outcomes (
    outcome_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    run_id UUID NOT NULL UNIQUE
        REFERENCES runs(run_id)
        ON DELETE CASCADE,

    verdict TEXT,

    tests_run BOOLEAN,
    tests_passed BOOLEAN,

    build_run BOOLEAN,
    build_passed BOOLEAN,

    merged BOOLEAN,
    reverted BOOLEAN,

    notes TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT outcomes_verdict_check
        CHECK (
            verdict IS NULL
            OR verdict IN (
                'accepted_as_is',
                'accepted_with_minor_changes',
                'accepted_with_major_changes',
                'rejected',
                'abandoned',
                'superseded'
            )
        )
);


-- ============================================================
-- USAGE METRICS
-- Stores token, cost, and iteration information when available.
--
-- Missing values must remain NULL.
-- Missing values must not be stored as zero.
-- ============================================================

CREATE TABLE usage_metrics (
    usage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    run_id UUID NOT NULL UNIQUE
        REFERENCES runs(run_id)
        ON DELETE CASCADE,

    input_tokens BIGINT,
    output_tokens BIGINT,
    cached_input_tokens BIGINT,

    cost_usd NUMERIC(12, 6),
    cost_source TEXT NOT NULL DEFAULT 'unavailable',

    iteration_count INTEGER,
    tool_calls_count INTEGER,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT usage_metrics_cost_source_check
        CHECK (
            cost_source IN (
                'reported',
                'estimated',
                'unavailable'
            )
        ),

    CONSTRAINT usage_metrics_input_tokens_check
        CHECK (
            input_tokens IS NULL
            OR input_tokens >= 0
        ),

    CONSTRAINT usage_metrics_output_tokens_check
        CHECK (
            output_tokens IS NULL
            OR output_tokens >= 0
        ),

    CONSTRAINT usage_metrics_cached_tokens_check
        CHECK (
            cached_input_tokens IS NULL
            OR cached_input_tokens >= 0
        ),

    CONSTRAINT usage_metrics_iteration_count_check
        CHECK (
            iteration_count IS NULL
            OR iteration_count >= 0
        )
);


-- ============================================================
-- INGESTION FAILURES
-- Records events that could not be processed successfully.
--
-- This prevents silent data loss.
-- ============================================================

CREATE TABLE ingestion_failures (
    failure_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    source TEXT,
    source_event_id TEXT,
    tool_id TEXT,

    error_type TEXT,
    error_message TEXT NOT NULL,

    raw_payload JSONB NOT NULL DEFAULT '{}',

    retry_count INTEGER NOT NULL DEFAULT 0,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,

    first_failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);


-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX idx_projects_status
    ON projects(status);

CREATE INDEX idx_repositories_project
    ON repositories(project_id);

CREATE INDEX idx_repositories_active
    ON repositories(is_active);

CREATE INDEX idx_runs_project
    ON runs(project_id);

CREATE INDEX idx_runs_repository
    ON runs(repository_id);

CREATE INDEX idx_runs_tool
    ON runs(tool_id);

CREATE INDEX idx_runs_status
    ON runs(status);

CREATE INDEX idx_runs_started_at
    ON runs(started_at DESC);

CREATE INDEX idx_runs_session
    ON runs(session_id);

CREATE INDEX idx_run_events_run
    ON run_events(run_id);

CREATE INDEX idx_run_events_tool
    ON run_events(tool_id);

CREATE INDEX idx_run_events_session
    ON run_events(session_id);

CREATE INDEX idx_run_events_type
    ON run_events(event_type);

CREATE INDEX idx_run_events_received_at
    ON run_events(received_at DESC);

CREATE INDEX idx_run_events_files_touched
    ON run_events
    USING GIN(files_touched);

CREATE INDEX idx_run_events_payload
    ON run_events
    USING GIN(raw_payload);

CREATE INDEX idx_commits_run
    ON commits(run_id);

CREATE INDEX idx_commits_repository
    ON commits(repository_id);

CREATE INDEX idx_commits_session
    ON commits(session_id);

CREATE INDEX idx_outcomes_verdict
    ON outcomes(verdict);

CREATE INDEX idx_ingestion_failures_resolved
    ON ingestion_failures(resolved);

CREATE INDEX idx_ingestion_failures_source_event
    ON ingestion_failures(source_event_id);


-- ============================================================
-- VIEWS
-- ============================================================

CREATE VIEW active_runs AS
SELECT
    run_id,
    project_id,
    repository_id,
    tool_id,
    model,
    session_id,
    human,
    machine_name,
    branch,
    worktree,
    cwd,
    task,
    intent,
    started_at
FROM runs
WHERE
    status = 'active'
    AND ended_at IS NULL;


CREATE VIEW run_overview AS
SELECT
    r.run_id,
    r.project_id,
    p.project_name,
    r.repository_id,
    repo.repository_name,
    r.tool_id,
    t.tool_name,
    r.model,
    r.session_id,
    r.human,
    r.branch,
    r.task,
    r.intent,
    r.summary,
    r.status,
    r.started_at,
    r.ended_at,

    o.verdict,
    o.tests_passed,
    o.build_passed,
    o.merged,
    o.reverted,

    u.input_tokens,
    u.output_tokens,
    u.cached_input_tokens,
    u.cost_usd,
    u.cost_source,
    u.iteration_count,
    u.tool_calls_count

FROM runs r

JOIN projects p
    ON p.project_id = r.project_id

JOIN repositories repo
    ON repo.repository_id = r.repository_id

JOIN tools t
    ON t.tool_id = r.tool_id

LEFT JOIN outcomes o
    ON o.run_id = r.run_id

LEFT JOIN usage_metrics u
    ON u.run_id = r.run_id;
