# Decision 0005: Repository registration + catch-all reclassification

## Status

Accepted

## Decision

Add a `POST /repositories` write endpoint for self-service repository
registration, and reclassify catch-all (`unsorted`) sessions into their real
repositories using a shared `cwd -> repository_id` override map consumed by both
the capture adapter and a host-local `reclassify` command.

## Reason

- 78 of 106 captured sessions sat in the catch-all because their projects were
  moved into the workspace tree, leaving the recorded cwd without a resolvable
  git remote. Re-extraction alone cannot reclassify them.
- A single override map keeps reclassification of stored runs and routing of
  future backfills consistent, so a full re-backfill never re-pollutes the
  catch-all.
- Reclassifying is a pure `UPDATE runs` (events hang off `run_id`); no schema
  change and no event rewrite.

## Boundaries

Reclassify is a host-local maintenance command (direct DB), not an API endpoint
— promote later only if a remote desktop or UI needs it. OTEL ingestion, a
generic `local_path` resolution layer, and `POST /projects` are out of scope.
See `docs/superpowers/specs/2026-06-21-capture-polish-design.md`.
