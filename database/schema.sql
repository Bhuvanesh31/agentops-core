CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE projects (
    project_id TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    owner TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE repositories (
    repository_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    repository_name TEXT NOT NULL,
    remote_url TEXT,
    local_path TEXT,
    default_branch TEXT NOT NULL DEFAULT 'main',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE tools (
    tool_id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    tool_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    repository_id TEXT NOT NULL REFERENCES repositories(repository_id),
    tool_id TEXT NOT NULL REFERENCES tools(tool_id),
    model TEXT,
    session_id TEXT,
    human TEXT,
    branch TEXT,
    worktree TEXT,
    task TEXT,
    intent TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (tool_id, session_id)
);

CREATE TABLE run_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID REFERENCES runs(run_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    tool_name TEXT,
    files_touched TEXT[] NOT NULL DEFAULT '{}',
    raw_payload JSONB NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE commits (
    commit_sha TEXT PRIMARY KEY,
    run_id UUID REFERENCES runs(run_id) ON DELETE SET NULL,
    repository_id TEXT NOT NULL REFERENCES repositories(repository_id),
    branch TEXT,
    author TEXT,
    committed_at TIMESTAMPTZ
);

CREATE TABLE outcomes (
    outcome_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL UNIQUE REFERENCES runs(run_id) ON DELETE CASCADE,
    verdict TEXT,
    tests_passed BOOLEAN,
    merged BOOLEAN,
    reverted BOOLEAN,
    notes TEXT,
    reviewed_at TIMESTAMPTZ
);

CREATE TABLE usage_metrics (
    usage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL UNIQUE REFERENCES runs(run_id) ON DELETE CASCADE,
    input_tokens BIGINT,
    output_tokens BIGINT,
    estimated_cost NUMERIC(12, 6),
    cost_source TEXT NOT NULL DEFAULT 'unavailable',
    iteration_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_repositories_project
    ON repositories(project_id);

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

CREATE INDEX idx_run_events_run
    ON run_events(run_id);

CREATE INDEX idx_run_events_type
    ON run_events(event_type);

CREATE INDEX idx_run_events_payload
    ON run_events USING GIN(raw_payload);

CREATE INDEX idx_commits_run
    ON commits(run_id);
