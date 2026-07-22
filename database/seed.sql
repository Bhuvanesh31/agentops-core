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
--
-- To register your own projects and repositories, either:
--   - Add INSERT blocks below (following the same pattern), or
--   - POST /repositories at runtime (see README)
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
    'your-username',
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
-- Update remote_url and local_path to match your environment.
-- local_path is host-specific — adjust per machine.
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
    'https://github.com/your-org/agentops-core.git',
    '/path/to/agentops_core',
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


-- ============================================================
-- LEADLE ECOSYSTEM
-- Projects and repositories for the Leadle product/platform work.
-- ============================================================

-- leadle project already seeded above (via leadle-os / leadle-content-studio).
-- Only repositories are added here.

INSERT INTO repositories (
    repository_id,
    project_id,
    repository_name,
    remote_url,
    local_path,
    default_branch,
    is_active
)
VALUES
    (
        'leadle-client-dashboard',
        'leadle',
        'Leadle Client Dashboard',
        'https://github.com/revops-leadle/leadle-client-dashboard.git',
        NULL,
        'main',
        TRUE
    ),
    (
        'leadle-mom-automation',
        'leadle',
        'Leadle MoM Automation',
        'https://github.com/Bhuvanesh31/leadle-mom-automation.git',
        '/home/bhuvanesh/AI_Native_Workspace/30-leadle-systems/leadle-mom-automation',
        'main',
        TRUE
    ),
    (
        'icp-sampler',
        'leadle',
        'ICP Sampler',
        'https://github.com/revops-leadle/icp-sampler.git',
        NULL,
        'main',
        TRUE
    ),
    (
        'ai-native-team',
        'leadle',
        'AI-Native Team Use-Cases',
        NULL,
        '/home/bhuvanesh/AI_Native_Workspace/30-leadle-systems/AI-native-team',
        'main',
        TRUE
    ),
    (
        'leadle-outbound',
        'leadle',
        'Leadle Outbound',
        NULL,
        '/home/bhuvanesh/AI_Native_Workspace/30-leadle-systems/leadle-outbound',
        'main',
        TRUE
    ),
    (
        'leadle-client-documentation',
        'leadle',
        'Leadle Client Documentation',
        NULL,
        '/home/bhuvanesh/AI_Native_Workspace/30-leadle-systems/leadle-client-documentation',
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
-- CONTENT INTELLIGENCE
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
    'content-intelligence',
    'Content Intelligence',
    'platform',
    'active',
    'bhuvanesh',
    'AI-native content intelligence tooling and corpus for Leadle/personal content workflows.'
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
    'bhuvanesh-content-intelligence',
    'content-intelligence',
    'Bhuvanesh Content Intelligence',
    'https://github.com/Bhuvanesh31/bhuvanesh-content-intelligence.git',
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
-- PERSONAL
-- Personal projects with no org affiliation.
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
    'personal',
    'Personal',
    'personal',
    'active',
    'bhuvanesh',
    'Personal experiments, dashboards, and content outside Leadle scope.'
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
VALUES
    (
        'claude-content-personal',
        'personal',
        'Claude Content (Personal)',
        'https://github.com/Bhuvanesh31/claude_content_personal.git',
        NULL,
        'main',
        TRUE
    ),
    (
        'bhuvanesh-channel-performance-dashboard',
        'personal',
        'Bhuvanesh Channel Performance Dashboard',
        NULL,
        '/home/bhuvanesh/AI_Native_Workspace/40-personal-systems/bhuvanesh-channel-performance-dashboard',
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
