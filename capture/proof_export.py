"""Content-safe proof export for AgentOps runs.

Produces a structured JSON summary of capture activity — counts, token totals,
repository rollups, recent run proof events (commit subjects only), and
reconciliation health — with no raw payloads, prompts, transcripts, diffs, or
file contents.

Usage:
  python -m capture.proof_export                        # prints JSON to stdout
  python -m capture.proof_export --output /tmp/proof.json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings

_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
_LAST_RECONCILE_PATH = _LOGS_DIR / "last_reconcile.json"

_EXPORT_SAFETY = {
    "raw_payloads_included": False,
    "prompts_included": False,
    "transcripts_included": False,
    "diffs_included": False,
    "file_contents_included": False,
    "secrets_included": False,
    "client_content_included": False,
}


def _load_last_reconcile() -> dict[str, Any] | None:
    try:
        return json.loads(_LAST_RECONCILE_PATH.read_text())
    except (OSError, ValueError):
        return None


def build_proof_summary(conn: psycopg.Connection) -> dict[str, Any]:
    """Query the DB and build the full proof export dict.

    Never reads raw_payload, prompt, transcript, diff, or file content columns.
    Token counts and commit subjects are the most granular data included.
    """
    # ── Totals ────────────────────────────────────────────────────────────────
    totals = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM runs) AS total_runs,
            (SELECT COUNT(*) FROM run_events) AS total_events,
            (SELECT SUM(input_tokens) FROM usage_metrics) AS input_tokens,
            (SELECT SUM(output_tokens) FROM usage_metrics) AS output_tokens,
            (SELECT SUM(cached_input_tokens) FROM usage_metrics) AS cached_input_tokens,
            (SELECT COUNT(DISTINCT repository_id) FROM runs) AS total_repositories_observed
        """
    ).fetchone()

    # ── Per-repository rollup ─────────────────────────────────────────────────
    # Four CTEs keep aggregates independent so joining commits (N per run) and
    # usage_metrics (1 per run) doesn't multiply token sums.
    repo_rows = conn.execute(
        """
        WITH run_stats AS (
            SELECT
                repository_id,
                project_id,
                COUNT(run_id) AS run_count,
                MAX(ended_at) AS latest_run_at
            FROM runs
            GROUP BY repository_id, project_id
        ),
        token_stats AS (
            SELECT
                r.repository_id,
                SUM(u.input_tokens)        AS input_tokens,
                SUM(u.output_tokens)       AS output_tokens,
                SUM(u.cached_input_tokens) AS cached_input_tokens
            FROM runs r
            LEFT JOIN usage_metrics u ON u.run_id = r.run_id
            GROUP BY r.repository_id
        ),
        event_stats AS (
            SELECT
                r.repository_id,
                COUNT(e.event_id) AS event_count
            FROM runs r
            LEFT JOIN run_events e ON e.run_id = r.run_id
            GROUP BY r.repository_id
        ),
        commit_stats AS (
            SELECT
                r.repository_id,
                COUNT(c.commit_sha)          AS linked_commit_count,
                COUNT(DISTINCT c.run_id)     AS runs_with_commits,
                MAX(c.committed_at)          AS latest_commit_at
            FROM runs r
            LEFT JOIN commits c ON c.run_id = r.run_id
            GROUP BY r.repository_id
        )
        SELECT
            rs.repository_id,
            rs.project_id,
            p.project_name,
            repo.repository_name,
            rs.run_count,
            COALESCE(es.event_count, 0)           AS event_count,
            ts.input_tokens,
            ts.output_tokens,
            ts.cached_input_tokens,
            COALESCE(cs.linked_commit_count, 0)   AS linked_commit_count,
            rs.latest_run_at,
            cs.latest_commit_at,
            CASE WHEN rs.run_count > 0
                 THEN ROUND(
                     COALESCE(cs.runs_with_commits, 0)::numeric / rs.run_count,
                     4
                 )
                 ELSE 0
            END AS reconciliation_coverage
        FROM run_stats rs
        JOIN projects p    ON p.project_id    = rs.project_id
        JOIN repositories repo ON repo.repository_id = rs.repository_id
        LEFT JOIN token_stats  ts ON ts.repository_id  = rs.repository_id
        LEFT JOIN event_stats  es ON es.repository_id  = rs.repository_id
        LEFT JOIN commit_stats cs ON cs.repository_id  = rs.repository_id
        ORDER BY rs.run_count DESC
        """
    ).fetchall()

    repositories = [
        {
            "project_id": r["project_id"],
            "project_name": r["project_name"],
            "repository_id": r["repository_id"],
            "repository_name": r["repository_name"],
            "run_count": r["run_count"],
            "event_count": r["event_count"],
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "cached_input_tokens": r["cached_input_tokens"],
            "linked_commit_count": r["linked_commit_count"],
            "latest_run_at": r["latest_run_at"].isoformat() if r["latest_run_at"] else None,
            "latest_commit_at": r["latest_commit_at"].isoformat() if r["latest_commit_at"] else None,
            "reconciliation_coverage": float(r["reconciliation_coverage"]),
        }
        for r in repo_rows
    ]

    # ── Recent proof events (commit subjects only, no payloads) ───────────────
    recent_rows = conn.execute(
        """
        SELECT
            r.run_id::text,
            r.project_id,
            r.started_at,
            r.ended_at,
            COALESCE(
                ARRAY_AGG(c.commit_message ORDER BY c.committed_at)
                FILTER (WHERE c.commit_sha IS NOT NULL),
                '{}'
            ) AS commit_subjects,
            COUNT(c.commit_sha) AS linked_commit_count
        FROM (
            SELECT run_id, project_id, started_at, ended_at
            FROM runs
            ORDER BY started_at DESC
            LIMIT 20
        ) r
        LEFT JOIN commits c ON c.run_id = r.run_id
        GROUP BY r.run_id, r.project_id, r.started_at, r.ended_at
        ORDER BY r.started_at DESC
        """
    ).fetchall()

    recent_proof_events = [
        {
            "project_id": row["project_id"],
            "run_id": row["run_id"],
            "started_at": row["started_at"].isoformat() if row["started_at"] else None,
            "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
            "linked_commit_count": row["linked_commit_count"],
            "commit_subjects": list(row["commit_subjects"] or []),
        }
        for row in recent_rows
    ]

    # ── Reconciliation health ─────────────────────────────────────────────────
    health_row = conn.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE r.ended_at IS NOT NULL) AS total_ended,
            COUNT(DISTINCT c.run_id) FILTER (
                WHERE r.ended_at IS NOT NULL
            ) AS runs_with_commits
        FROM runs r
        LEFT JOIN commits c ON c.run_id = r.run_id
        """
    ).fetchone()

    total_ended = health_row["total_ended"] or 0
    runs_with_commits = health_row["runs_with_commits"] or 0
    runs_without_commits = total_ended - runs_with_commits

    last_rec = _load_last_reconcile()
    if last_rec:
        skipped_stale     = last_rec.get("skipped_stale_cwd")
        skipped_non_git   = last_rec.get("skipped_non_git_cwd")
        skipped_branch    = last_rec.get("skipped_missing_branch")
        failed_unexpected = last_rec.get("failed_unexpected")
        next_action = (
            "up to date"
            if failed_unexpected == 0
            else f"investigate: {failed_unexpected} unexpected failures in last reconcile"
        )
    else:
        skipped_stale = skipped_non_git = skipped_branch = failed_unexpected = None
        next_action = "run: python -m capture.git.reconcile --all"

    reconciliation_health = {
        "runs_with_commits": runs_with_commits,
        "runs_without_commits": runs_without_commits,
        "skipped_stale_cwd": skipped_stale,
        "skipped_non_git_cwd": skipped_non_git,
        "skipped_missing_branch": skipped_branch,
        "failed_unexpected": failed_unexpected,
        "next_action": next_action,
    }

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1",
        "total_runs": totals["total_runs"],
        "total_events": totals["total_events"],
        "total_tokens": {
            "input": totals["input_tokens"],
            "output": totals["output_tokens"],
            "cached_input": totals["cached_input_tokens"],
        },
        "total_repositories_observed": totals["total_repositories_observed"],
        "repositories": repositories,
        "recent_proof_events": recent_proof_events,
        "reconciliation_health": reconciliation_health,
        "export_safety": _EXPORT_SAFETY,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export a content-safe AgentOps proof summary as JSON"
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        metavar="PATH",
        help="Write JSON to PATH instead of stdout",
    )
    args = parser.parse_args(argv)

    try:
        db_url = get_settings().database_url
    except Exception as exc:
        print(f"ERROR: could not load settings: {exc}", file=sys.stderr)
        return 1

    try:
        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            summary = build_proof_summary(conn)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(summary, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(payload)
        print(f"Proof summary written to {args.output}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
