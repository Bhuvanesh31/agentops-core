"""Reconcile git commits with captured runs.

Usage:
  python -m capture.git.reconcile --all
  python -m capture.git.reconcile --repo <repository_id>
  python -m capture.git.reconcile --repo <repository_id> --dry-run
  python -m capture.git.reconcile --all --verbose
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings
from capture.git.query import query_commits


def _cwd_skip_reason(cwd: str) -> str | None:
    """Return a skip-reason string if the cwd cannot be used for git log.

    Returns:
      'stale_cwd'    — path does not exist on disk
      'non_git_cwd'  — path exists but is not inside a git repository
      None           — cwd looks usable; proceed to branch check
    """
    if not os.path.exists(cwd):
        return "stale_cwd"
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-dir"],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "non_git_cwd"
    return None if r.returncode == 0 else "non_git_cwd"


def _branch_exists(cwd: str, branch: str) -> bool:
    """Return True if branch resolves in the repo at cwd."""
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--verify", branch],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


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
    verbose: bool = False,
) -> dict:
    """Query git log for each eligible run and upsert commits.

    Processes runs newest-ended-first so the most-recently-completed run wins
    when a commit falls within two overlapping windows (ON CONFLICT DO NOTHING
    preserves the first-inserted link).

    Pre-checks each run's cwd before calling git to classify expected skips
    (stale paths, deleted worktrees, missing branches) without emitting noisy
    git error output. Uses a date-window fallback across all local refs when
    the named branch no longer exists locally.

    Returns a dict with:
      runs_processed, commits_linked,
      skipped_stale_cwd, skipped_non_git_cwd, skipped_missing_branch,
      failed_unexpected, dry_run
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

    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception as exc:
        print(f"[git-reconcile] DB query failed: {exc}", file=sys.stderr)
        return {
            "runs_processed": 0, "commits_linked": 0,
            "skipped_stale_cwd": 0, "skipped_non_git_cwd": 0,
            "skipped_missing_branch": 0, "failed_unexpected": 0,
            "dry_run": dry_run,
        }

    runs_processed = 0
    commits_linked = 0
    skipped_stale_cwd = 0
    skipped_non_git_cwd = 0
    skipped_missing_branch = 0
    failed_unexpected = 0

    for row in rows:
        cwd = row["cwd"]
        branch = row["branch"]
        run_short = str(row["run_id"])[:8]

        # ── Pre-checks: classify expected skips without running git log ───────
        skip = _cwd_skip_reason(cwd)
        if skip == "stale_cwd":
            skipped_stale_cwd += 1
            if verbose:
                print(f"[{run_short}] skip stale_cwd: {cwd}")
            continue
        if skip == "non_git_cwd":
            skipped_non_git_cwd += 1
            if verbose:
                print(f"[{run_short}] skip non_git_cwd: {cwd}")
            continue

        try:
            before = row["ended_at"] + timedelta(minutes=30)

            if not _branch_exists(cwd, branch):
                # Branch was deleted or is a stale worktree ref; fall back to
                # searching all local refs in the same time window.
                commits = query_commits(cwd, None, row["started_at"], before)
                if not commits:
                    skipped_missing_branch += 1
                    if verbose:
                        print(f"[{run_short}] skip missing_branch: {branch!r} in {cwd}")
                    continue
            else:
                commits = query_commits(cwd, branch, row["started_at"], before)
                if not commits:
                    continue  # no commits in window — not a skip, just quiet

            runs_processed += 1
            if not dry_run:
                n = upsert_commits(
                    conn, row["run_id"], row["repository_id"], branch, commits
                )
                commits_linked += n
            else:
                commits_linked += len(commits)

            if verbose:
                prefix = "[dry-run] " if dry_run else ""
                print(
                    f"{prefix}[{run_short}] branch={branch} "
                    f"cwd={cwd} → {len(commits)} commits"
                )

        except Exception as exc:
            failed_unexpected += 1
            print(
                f"[git-reconcile] error on run {row['run_id']}: {exc}",
                file=sys.stderr,
            )

    return {
        "runs_processed": runs_processed,
        "commits_linked": commits_linked,
        "skipped_stale_cwd": skipped_stale_cwd,
        "skipped_non_git_cwd": skipped_non_git_cwd,
        "skipped_missing_branch": skipped_missing_branch,
        "failed_unexpected": failed_unexpected,
        "dry_run": dry_run,
    }


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
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print per-run details (default: summary only)",
    )
    args = parser.parse_args(argv)

    try:
        db_url = get_settings().database_url
    except Exception as exc:
        print(f"ERROR: could not load settings: {exc}", file=sys.stderr)
        return 1

    try:
        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            result = reconcile_runs(
                conn,
                repository_id=args.repo,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    dr = " (dry-run)" if result["dry_run"] else ""
    print(
        f"\nruns_processed={result['runs_processed']} "
        f"commits_linked={result['commits_linked']}{dr}\n"
        f"skipped_stale_cwd={result['skipped_stale_cwd']} "
        f"skipped_non_git_cwd={result['skipped_non_git_cwd']} "
        f"skipped_missing_branch={result['skipped_missing_branch']} "
        f"failed_unexpected={result['failed_unexpected']}"
    )
    if not result["dry_run"]:
        try:
            logs_dir = Path(__file__).resolve().parents[2] / "logs"
            logs_dir.mkdir(exist_ok=True)
            (logs_dir / "last_reconcile.json").write_text(json.dumps(result))
        except OSError:
            pass  # non-fatal; proof export will show nulls for skip counters
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
