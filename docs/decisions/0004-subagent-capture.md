# Decision 0004: Sub-agent capture merges into the parent run

## Status

Accepted

## Decision

Claude Code sub-agent (Task/Agent-tool) transcripts — stored at
`<slug>/<parent-session>/subagents/agent-*.jsonl` — are discovered and their
events merged into the parent session's run. Sub-agents share the parent's
`sessionId`, so the existing run key attaches them automatically.

## Reason

- Sub-agents are part of one Claude Code session, not separate sessions; merging
  keeps a run's total cost/tokens complete.
- Agent attribution (`agentId`, `attributionAgent`, `isSidechain`) is preserved
  in `run_events.raw_payload` and is queryable via the existing GIN index, so no
  schema change is needed in the capture phase.

## Boundaries

No `parent_run_id`, no per-sub-agent runs, no schema migration. Promoting agent
attribution to columns or a parent→child tree is deferred to the registry /
insights milestone. See
`docs/superpowers/specs/2026-06-19-subagent-capture-design.md`.
