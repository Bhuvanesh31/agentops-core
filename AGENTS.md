# AgentOps Core — Agent Instructions

## Project purpose

AgentOps Core captures work performed by Claude Code, Codex, and future AI development tools across multiple repositories.

It provides the shared data foundation for:

* AI Work Journal
* Agent Registry
* Agent Evaluator
* Agent Usage and Efficiency Intelligence
* Cross-repository search
* Claude Code versus Codex comparison

## Current phase

The project is currently in the architecture and foundation phase.

Do not build the full user interface, evaluator, or recommendation engine yet.

## Initial scope

The first version must support:

1. Project and repository registration
2. Claude Code session capture
3. Codex session capture
4. Git commit reconciliation
5. PostgreSQL storage
6. Run and event normalization
7. Basic structured search
8. Manual outcome and acceptance capture
9. AI Work Journal integration
10. Secret redaction before storage

## Architecture rules

* PostgreSQL is the system of record.
* Git is the provenance and fallback capture layer.
* Qdrant is optional and must remain a rebuildable search index.
* The AI Work Journal reads from AgentOps Core.
* The AI Work Journal must not create a separate session-capture system.
* Tool-specific payloads must be normalized into one shared event format.
* Missing token or cost data must be stored as unavailable, not as zero.
* Sensitive information must be redacted before persistence.
* Every project and repository must have a stable ID.

## Development rules

* Make small, reviewable changes.
* Do not modify unrelated files.
* Do not commit credentials or API keys.
* Add tests for normalization and ingestion logic.
* Record architecture decisions inside `docs/decisions`.
* Explain assumptions when tool telemetry is unavailable.
* Do not claim that an integration works until it has been tested.

## Initial build order

1. Define project and repository identity.
2. Define the normalized run-event schema.
3. Create the PostgreSQL schema.
4. Build the FastAPI ingestion endpoint.
5. Add Claude Code capture.
6. Add Codex capture.
7. Add Git commit reconciliation.
8. Add basic search.
9. Connect the AI Work Journal.
10. Add evaluation and analytics only after reliable data exists.

## Definition of done for the first milestone

The first milestone is complete when:

* Claude Code activity can be captured.
* Codex activity can be captured.
* Both tools write into the same PostgreSQL database.
* Runs are connected to the correct project and repository.
* Git commits can be connected to runs.
* Duplicate events do not create duplicate records.
* Failed ingestion is visible.
* Sensitive values are redacted.
* Runs can be searched across at least two repositories.
