# Decision 0003: Claude Code capture via transcript replay

## Status

Accepted

## Decision

Claude Code activity is captured by replaying the local JSONL session
transcripts (`~/.claude/projects/**/*.jsonl`) through the ingestion API, not via
hooks or OTEL in v1.

## Reason

- Transcripts are the only source that can backfill existing sessions and the
  only source carrying `cwd` + `gitBranch` (needed to attribute a session to a
  repository) and full prompt/response content.
- Per-line `uuid`s map to `run_events.source_event_id`, so replay is idempotent
  and safe across desktops.

## Identity

Repositories are keyed by canonical git remote URL (stable across machines),
resolved via `GET /repositories`. Unregistered remotes are quarantined to a
pending report; folders without a remote are skipped (opt-in only).

## Boundaries

v1 captures normalized meaningful events only. OTEL is a planned complementary
enrichment path (authoritative cost + real-time), joined on `session.id` /
`request_id`. See `docs/superpowers/specs/2026-06-19-claude-code-capture-design.md`.
