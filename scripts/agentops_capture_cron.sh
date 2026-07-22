#!/usr/bin/env bash
# ============================================================
# AgentOps Core — scheduled Claude Code capture
#
# Designed for cron. Loads .env without printing secrets,
# checks API health before running, and appends all output
# to logs/agentops_capture.log (gitignored).
#
# Secret-safe: .env values are never echoed or logged.
#
# Exit codes:
#   0  Capture ran (including all-duplicate or no-op runs),
#      or the API was unreachable (capture deferred, not failed)
#   1  Config or setup error that requires human action
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
LOG_DIR="$ROOT_DIR/logs"
LOG_FILE="$LOG_DIR/agentops_capture.log"
PYTHON="$ROOT_DIR/.venv/bin/python"
API_URL="${AGENTOPS_API_URL:-http://localhost:8000}"

mkdir -p "$LOG_DIR"

log() {
    printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_FILE"
}

# ── Guards (configuration errors — exit 1 so cron can alert) ─────────────
if [[ ! -f "$ENV_FILE" ]]; then
    log "ERROR: .env not found at $ENV_FILE — copy .env.example and fill in values."
    exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
    log "ERROR: Python not found at $PYTHON"
    log "       Run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
    exit 1
fi

# ── Load environment silently (never print values) ────────────────────────
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# ── API health pre-check (unavailable = defer, not fail) ──────────────────
if ! curl -sf --max-time 5 "$API_URL/health" >/dev/null 2>&1; then
    log "SKIP: $API_URL not reachable — capture deferred to next scheduled run."
    exit 0
fi

# ── Capture ───────────────────────────────────────────────────────────────
log "START: Claude Code capture (api=$API_URL)"
cd "$ROOT_DIR"

CAPTURE_ARGS=(--api-url "$API_URL")
CATCH_ALL="${AGENTOPS_CATCH_ALL_REPO:-}"
if [[ -n "$CATCH_ALL" ]]; then
    CAPTURE_ARGS+=(--catch-all "$CATCH_ALL")
fi

# The capture CLI returns 0 for all normal states (created, duplicate, skipped).
# An unexpected non-zero exit means an unhandled exception; log it and continue.
if ! "$PYTHON" -m capture.claude_code "${CAPTURE_ARGS[@]}" >> "$LOG_FILE" 2>&1; then
    log "WARN: capture process exited non-zero — check $LOG_FILE for details."
fi

log "END: capture run complete"
