# AgentOps Core — Operations

Runbook for keeping capture running day-to-day.

---

## One-time setup

```bash
# 1. Copy and fill in credentials
cp .env.example .env
# Edit .env: set POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB / POSTGRES_PORT

# 2. Create the virtualenv and install the package
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 3. Start the stack (Postgres + API)
docker compose up -d --build api

# 4. Apply schema and seed data (idempotent)
set -a; . ./.env; set +a
docker exec -i agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 < database/schema.sql
docker exec -i agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 < database/seed.sql

# 5. Verify the stack is healthy
./scripts/db_healthcheck.sh
curl -s http://localhost:8000/health
```

---

## Manual capture

### Dry-run (safe, never writes)

```bash
set -a; . ./.env; set +a
.venv/bin/python -m capture.claude_code --dry-run
```

Shows what would be captured without touching the database.

### Real capture

```bash
set -a; . ./.env; set +a
.venv/bin/python -m capture.claude_code
```

Capture is idempotent — re-running the same transcripts records duplicates and skips them silently.

---

## Scheduled capture (cron)

### How it works

`scripts/agentops_capture_cron.sh` wraps the capture CLI for unattended use:

- Loads `.env` without printing any value
- Pre-checks `GET /health` — if the API is down, logs `SKIP` and exits 0 (no alert)
- Runs the capture; appends all output to `logs/agentops_capture.log`
- Exits 0 for all normal outcomes (created, duplicate, skipped, API unavailable)
- Exits 1 only for config errors (missing `.env`, missing virtualenv)

### Dry-run the script itself

```bash
bash scripts/agentops_capture_cron.sh
```

No `--dry-run` flag is passed here, so this is a real capture. To preview without writing, call the capture CLI directly with `--dry-run` (see above).

### Cron installation

Do not install automatically — copy this line and add it manually with `crontab -e`:

```cron
# AgentOps: capture Claude Code transcripts every 30 minutes
*/30 * * * * /home/bhuvanesh/AI_Native_Workspace/10-platform/agentops_core/scripts/agentops_capture_cron.sh
```

Adjust the path and interval to match your setup. Every 30 minutes is a reasonable default; the capture is idempotent so running more often is safe.

### Log location

```
logs/agentops_capture.log
```

This path is gitignored. Each run appends timestamped lines:

```
[2026-07-22T10:30:01Z] START: Claude Code capture (api=http://localhost:8000)
[2026-07-22T10:30:03Z] END: capture run complete
```

Rotate with `logrotate` or truncate manually — there is no automatic rotation.

---

## Verification

After capture runs, confirm data landed:

```bash
# Per-project run counts and token totals
curl -s http://localhost:8000/overview | python3 -m json.tool

# Most recent runs
curl -s "http://localhost:8000/runs?limit=5" | python3 -m json.tool

# Or open the browser UI
open http://localhost:8000/ui/
```

---

## Git commit reconciliation

Reconciliation links git commits to the run that produced them. It runs automatically after every capture. To backfill historical runs:

```bash
set -a; . ./.env; set +a
.venv/bin/python -m capture.git.reconcile --all        # all repos
.venv/bin/python -m capture.git.reconcile --repo <id>  # one repo
```

Add `--dry-run` to preview without writing.

---

## Stack management

```bash
# Start
docker compose up -d --build api

# Stop (preserves Postgres volumes)
docker compose down

# NEVER run: docker compose down -v  (destroys the database)
```
