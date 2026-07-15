"""Reconcile git commits with captured runs.

Usage:
  python -m capture.git.reconcile --all
  python -m capture.git.reconcile --repo <repository_id>
  python -m capture.git.reconcile --repo <repository_id> --dry-run
"""

import argparse
import sys
from datetime import timedelta

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings
from capture.git.query import query_commits


def upsert_commits(
    conn: psycopg.Connection,
    run_id: object,
    repository_id: str,
    branch: str,
    commits: list[dict],
) -> int:
    """Insert commits for a run. ON CONFLICT (commit_sha) DO NOTHING.

    Returns the count of rows actually inserted (0 for each conflict).
    """
    inserted = 0
    for c in commits:
        cursor = conn.execute(
            """
            INSERT INTO commits
                (commit_sha, run_id, repository_id, branch,
                 author_name, author_email, commit_message, committed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (commit_sha) DO NOTHING
            """,
            (
                c["commit_sha"],
                run_id,
                repository_id,
                branch,
                c.get("author_name"),
                c.get("author_email"),
                c.get("commit_message"),
                c.get("committed_at"),
            ),
        )
        inserted += cursor.rowcount
    return inserted


def reconcile_runs(
    conn: psycopg.Connection,
    repository_id: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Query git log for each eligible run and upsert commits.

    Processes runs newest-ended-first so the most-recently-completed run wins
    when a commit falls within two overlapping windows (ON CONFLICT DO NOTHING
    preserves the first-inserted link).

    Returns {"runs_processed": int, "commits_linked": int}.
    """
    sql = """
        SELECT run_id, repository_id, branch, cwd, started_at, ended_at
        FROM runs
        WHERE ended_at IS NOT NULL
          AND branch IS NOT NULL
          AND cwd IS NOT NULL
    """
    params: list = []
    if repository_id is not None:
        sql += " AND repository_id = %s"
        params.append(repository_id)
    sql += " ORDER BY ended_at DESC"

    rows = conn.execute(sql, params).fetchall()

    runs_processed = 0
    commits_linked = 0

    for row in rows:
        try:
            before = row["ended_at"] + timedelta(minutes=30)
            commits = query_commits(
                row["cwd"], row["branch"], row["started_at"], before
            )
            if not commits:
                continue

            runs_processed += 1
            if not dry_run:
                n = upsert_commits(
                    conn, row["run_id"], row["repository_id"], row["branch"], commits
                )
                commits_linked += n
            else:
                commits_linked += len(commits)

            run_short = str(row["run_id"])[:8]
            prefix = "[dry-run] " if dry_run else ""
            print(
                f"{prefix}[{run_short}] branch={row['branch']} "
                f"cwd={row['cwd']} → {len(commits)} commits"
            )
        except Exception as exc:
            print(
                f"[git-reconcile] error on run {row['run_id']}: {exc}",
                file=sys.stderr,
            )

    return {"runs_processed": runs_processed, "commits_linked": commits_linked}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile git commits with AgentOps captured runs"
    )
    parser.add_argument(
        "--repo",
        default=None,
        metavar="REPOSITORY_ID",
        help="Reconcile only runs for this repository (default: all)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Reconcile all repositories (default when --repo is omitted)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be linked; do not write to the database",
    )
    args = parser.parse_args(argv)

    try:
        db_url = get_settings().database_url
    except Exception as exc:
        print(f"ERROR: could not load settings: {exc}", file=sys.stderr)
        return 1

    try:
        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            result = reconcile_runs(conn, repository_id=args.repo, dry_run=args.dry_run)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"\nDone. runs_processed={result['runs_processed']} "
        f"commits_linked={result['commits_linked']}"
        + (" (dry-run)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
