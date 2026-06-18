# AgentOps Core Architecture

## Purpose

AgentOps Core is the shared telemetry and storage layer for AI-assisted work performed across multiple repositories.

## Initial flow

```text
Claude Code
     \
      → Capture adapters
     /
Codex

      ↓

Normalized run events

      ↓

FastAPI ingestion service

      ↓

PostgreSQL system of record

      ↓

AI Work Journal
Search
Evaluation
Usage analytics
```

## Storage responsibilities

### PostgreSQL

Authoritative storage for:

* Projects
* Repositories
* Agent runs
* Raw events
* Git commits
* Outcomes
* Human verdicts
* Available token and cost information

### Git

Used for:

* Commit provenance
* Branch history
* Change review
* Fallback capture when tool hooks fail

Git is not the central run-event database.

### Qdrant

Optional semantic search projection.

Qdrant must be rebuildable from PostgreSQL and must never become the authoritative source.

## Initial entities

* Project
* Repository
* Run
* Run event
* Commit
* Outcome
* Tool
* Model
* Human verdict

## Initial consumers

1. AI Work Journal
2. Cross-repository search
3. Claude Code versus Codex comparison
4. Agent Evaluator
5. Cost and efficiency analytics

## Current boundaries

Not included in the first milestone:

* Multi-user permissions
* Slack collision alerts
* Autonomous task orchestration
* Tool recommendation engine
* Full agent registry interface
* Single agent-quality score

```
```
