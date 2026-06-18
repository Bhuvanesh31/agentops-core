#!/usr/bin/env bash
# ============================================================
# AgentOps Core — database health check
#
# Verifies that the PostgreSQL service is reachable and that the
# schema and reference seed are present. Intended to be run after
# `docker compose up` and after applying schema.sql / seed.sql.
#
# Secret-safe: loads .env into the environment but never prints
# any value from it. Only key names and row counts are shown.
#
# Exit codes:
#   0  Postgres reachable and connectivity query passed
#   1  configuration missing or Postgres unreachable
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
CONTAINER="${AGENTOPS_PG_CONTAINER:-agentops-postgres}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found. Copy .env.example to .env first." >&2
    exit 1
fi

# Load env without echoing values.
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

: "${POSTGRES_USER:?POSTGRES_USER not set in .env}"
: "${POSTGRES_DB:?POSTGRES_DB not set in .env}"

# Run a query and return the bare scalar result.
run_psql() {
    docker exec -i "$CONTAINER" \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"
}

echo "AgentOps DB health check"
echo "------------------------"

# 1. Postgres accepting connections inside the container.
if ! docker exec "$CONTAINER" \
        pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    echo "FAIL : Postgres not ready in container '$CONTAINER'." >&2
    exit 1
fi
echo "OK   : Postgres accepting connections"

# 2. Connectivity query returns the expected value.
if [[ "$(run_psql 'SELECT 1;')" != "1" ]]; then
    echo "FAIL : connectivity query did not return 1." >&2
    exit 1
fi
echo "OK   : connectivity query"

# 3. Schema presence (informational — 9 tables, 2 views expected).
table_count="$(run_psql "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE';")"
view_count="$(run_psql "SELECT count(*) FROM information_schema.views WHERE table_schema='public';")"
echo "INFO : base tables = ${table_count} (expected 9)"
echo "INFO : views       = ${view_count} (expected 2)"

# 4. Reference seed presence (informational).
projects="$(run_psql 'SELECT count(*) FROM projects;')"
repositories="$(run_psql 'SELECT count(*) FROM repositories;')"
tools="$(run_psql 'SELECT count(*) FROM tools;')"
echo "INFO : seed -> projects=${projects} repositories=${repositories} tools=${tools}"

echo "------------------------"
echo "Health check passed."
