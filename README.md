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
