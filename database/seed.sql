-- ============================================================
-- AgentOps Core
-- Reference seed data
--
-- Idempotent: safe to run repeatedly.
-- Seeds the stable reference records the ingestion service
-- depends on before any run can be recorded:
--   - the AgentOps Core project
--   - the AgentOps Core repository
--   - the Claude Code tool
--   - the Codex tool
--
-- Re-running this file keeps these records authoritative:
-- ON CONFLICT updates the descriptive columns rather than
-- erroring or silently skipping. It never deletes data and
-- never touches runs, events, or any captured telemetry.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- PROJECT
-- IDs come from .ai/project.yaml so they stay stable across
-- environments and match what capture adapters will send.
-- ------------------------------------------------------------
INSERT INTO projects (
    project_id,
    project_name,
    category,
    status,
    owner,
    description
)
VALUES (
    'agentops-core',
    'AgentOps Core',
    'platform',
    'active',
    'bhuvanesh',
    'Shared telemetry and storage layer capturing Claude Code, Codex, and future AI development tool activity across repositories.'
)
ON CONFLICT (project_id) DO UPDATE SET
    project_name = EXCLUDED.project_name,
    category     = EXCLUDED.category,
    status       = EXCLUDED.status,
    owner        = EXCLUDED.owner,
    description  = EXCLUDED.description,
    updated_at   = NOW();


-- ------------------------------------------------------------
-- REPOSITORY
-- local_path is machine-specific by nature; adjust per host.
-- ------------------------------------------------------------
INSERT INTO repositories (
    repository_id,
    project_id,
    repository_name,
    remote_url,
    local_path,
    default_branch,
    is_active
)
VALUES (
    'agentops-core-main',
    'agentops-core',
    'AgentOps Core',
    'https://github.com/Bhuvanesh31/agentops-core.git',
    '/home/bhuvanesh/AI_Native_Workspace/10-platform/agentops_core',
    'main',
    TRUE
)
ON CONFLICT (repository_id) DO UPDATE SET
    project_id      = EXCLUDED.project_id,
    repository_name = EXCLUDED.repository_name,
    remote_url      = EXCLUDED.remote_url,
    local_path      = EXCLUDED.local_path,
    default_branch  = EXCLUDED.default_branch,
    is_active       = EXCLUDED.is_active,
    updated_at      = NOW();


-- ------------------------------------------------------------
-- CATCH-ALL PROJECT + REPOSITORY
-- Captures sessions from working folders that have no git remote
-- (planning/notes dirs, repos without a remote, deleted folders).
-- Sessions are routed here by the capture adapter's --catch-all flag;
-- the original folder is preserved per-run in runs.cwd for later
-- reclassification. remote_url is NULL by design (no remote).
-- ------------------------------------------------------------
INSERT INTO projects (
    project_id,
    project_name,
    category,
    status,
    owner,
    description
)
VALUES (
    'unsorted',
    'Unsorted',
    'uncategorized',
    'active',
    'bhuvanesh',
    'Catch-all for agent sessions run outside a git-remote-backed repository; reclassify by cwd.'
)
ON CONFLICT (project_id) DO UPDATE SET
    project_name = EXCLUDED.project_name,
    category     = EXCLUDED.category,
    status       = EXCLUDED.status,
    owner        = EXCLUDED.owner,
    description  = EXCLUDED.description,
    updated_at   = NOW();

INSERT INTO repositories (
    repository_id,
    project_id,
    repository_name,
    remote_url,
    local_path,
    default_branch,
    is_active
)
VALUES (
    'unsorted-local',
    'unsorted',
    'Unsorted (local, no remote)',
    NULL,
    NULL,
    'main',
    TRUE
)
ON CONFLICT (repository_id) DO UPDATE SET
    project_id      = EXCLUDED.project_id,
    repository_name = EXCLUDED.repository_name,
    remote_url      = EXCLUDED.remote_url,
    local_path      = EXCLUDED.local_path,
    default_branch  = EXCLUDED.default_branch,
    is_active       = EXCLUDED.is_active,
    updated_at      = NOW();


-- ------------------------------------------------------------
-- TOOLS
-- Stable slugs (claude-code, codex) are the tool_id values
-- that runs, run_events, and commits reference.
-- ------------------------------------------------------------
INSERT INTO tools (
    tool_id,
    tool_name,
    provider,
    description
)
VALUES
    (
        'claude-code',
        'Claude Code',
        'Anthropic',
        'Anthropic''s agentic command-line coding tool.'
    ),
    (
        'codex',
        'Codex',
        'OpenAI',
        'OpenAI''s agentic coding tool.'
    )
ON CONFLICT (tool_id) DO UPDATE SET
    tool_name   = EXCLUDED.tool_name,
    provider    = EXCLUDED.provider,
    description = EXCLUDED.description;

COMMIT;
