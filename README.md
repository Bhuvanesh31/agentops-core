# AgentOps Core

AgentOps Core captures work performed by Claude Code, Codex and future AI development tools.

Initial scope:

- Capture sessions automatically
- Store runs centrally
- Connect runs to repositories and commits
- Search activity across projects
- Feed the AI Work Journal
- Compare Claude Code and Codex outcomes

## Local database

PostgreSQL is the system of record and runs through Docker Compose.

### Configure

```bash
cp .env.example .env   # then fill in POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB / POSTGRES_PORT
```

`.env` is git-ignored and must never be committed. Secrets stay local.

### Start PostgreSQL

```bash
docker compose up -d
```

### Check container health

```bash
docker compose ps
```

### Apply the schema

```bash
set -a; . ./.env; set +a
docker exec -i agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 < database/schema.sql
```

### Apply the reference seed (idempotent — safe to re-run)

```bash
set -a; . ./.env; set +a
docker exec -i agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 < database/seed.sql
```

### List tables

```bash
set -a; . ./.env; set +a
docker exec agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"
```

### List views

```bash
set -a; . ./.env; set +a
docker exec agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dv"
```

### Run the health check

```bash
./scripts/db_healthcheck.sh
```

Verifies that Postgres is reachable and reports table, view, and seed counts.
It never prints any value from `.env`.

## Ingestion API

A FastAPI service normalizes Claude Code / Codex events and stores them in
PostgreSQL. It exposes `GET /health` and `POST /runs/events`.

### Start the API

With Docker Compose (builds the image, waits for Postgres to be healthy):

```bash
docker compose up -d --build api
```

For local development without Docker (Postgres must already be running):

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
set -a; . ./.env; set +a
.venv/bin/uvicorn app.main:app --reload --port 8000
```

The API reads the same `POSTGRES_*` variables as the database. On the host it
defaults to `localhost:${POSTGRES_PORT}`; the Compose `api` service overrides
these to reach Postgres at `postgres:5432`.

### Check API health

```bash
curl -s http://localhost:8000/health
```

Healthy response (HTTP 200):

```json
{"status": "ok", "api": "ok", "database": "reachable"}
```

Returns HTTP 503 with `"database": "unreachable"` if Postgres cannot be reached.

### Send a sample event

```bash
curl -s -X POST http://localhost:8000/runs/events \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "claude-code",
    "session_id": "demo-session-1",
    "event_type": "session_started",
    "repository_id": "agentops-core-main",
    "project_id": "agentops-core",
    "model": "claude-opus-4-8",
    "branch": "main",
    "cwd": "/path/to/repo",
    "intent": "demo",
    "files_touched": ["app/main.py"],
    "raw_payload": {"api_key": "sk-ant-EXAMPLE", "note": "hello"},
    "source_event_id": "demo-evt-1"
  }'
```

Response (HTTP 201 on first insert):

```json
{
  "run_id": "…uuid…",
  "event_id": "…uuid…",
  "status": "created",
  "redaction_status": "redacted"
}
```

Re-sending the same `source_event_id` returns HTTP 200 with
`"status": "duplicate"` and does not insert a second event. Secrets in
`raw_payload` (here `api_key`) are redacted to `[REDACTED]` before storage.

Interactive API docs are available at `http://localhost:8000/docs`.

### Run the tests

Tests run against the live Postgres container and clean up after themselves.

```bash
set -a; . ./.env; set +a
.venv/bin/pytest
```

### Format and lint

```bash
.venv/bin/ruff format app tests
.venv/bin/ruff check app tests
```

## Claude Code capture

Replay local Claude Code transcripts into AgentOps Core (idempotent; safe to
re-run). The API must be running and the session's repository must be registered.

Preview without writing anything:

```bash
.venv/bin/python -m capture.claude_code --api-url http://localhost:8000 --dry-run
```

Ingest for real:

```bash
.venv/bin/python -m capture.claude_code --api-url http://localhost:8000
```

Useful flags: `--only <folder-substring>`, `--since <epoch>`,
`--projects-dir <path>`. Repositories that aren't registered yet are listed as
"pending" — add them to `database/seed.sql`, re-apply the seed, and re-run.

Sub-agent (Task/Agent-tool) sessions are captured automatically and merged into
their parent session's run. Slice sub-agent activity with
`raw_payload->>'isSidechain' = 'true'`, grouped by `raw_payload->>'attributionAgent'`.

### Repository registration & reclassification

Register a repository (so its sessions attribute to a real project) with
`POST /repositories` (`repository_id`, `project_id`, `repository_name`, and
optional `remote_url` / `local_path`). Sessions resolve to a repository by their
canonical git remote; sessions whose recorded cwd has no remote (moved or
deleted folders) are mapped by `capture/claude_code/cwd_overrides.toml`
(`cwd -> repository_id`), which the capture adapter consults before the
catch-all. To re-point already-stored catch-all runs, run the host-local
command: `python -m capture.claude_code.reclassify`.

### Run timestamps

`runs.started_at` and `runs.ended_at` reflect real session time, derived from
events' `occurred_at`: each ingest folds the event time into the run's bounds
(`started_at = min`, `ended_at = max`), while `created_at`/`updated_at` remain
write-time bookkeeping. To recompute the bounds for runs already stored (e.g.
after importing historical events), run the host-local command:
`python -m app.maintenance`. It is idempotent — a second run updates 0 rows.
