"""Query git log for commits within a time window."""

import subprocess
import sys
from datetime import datetime


def query_commits(
    cwd: str,
    branch: str,
    after: datetime,
    before: datetime,
) -> list[dict]:
    """Return commits on branch whose author date falls in (after, before].

    Uses ``git log`` in the given working directory. Returns an empty list on
    any error (missing cwd, git not found, non-zero exit, parse failure) and
    logs the reason to stderr. Never raises.
    """
    try:
        result = subprocess.run(
            [
                "git", "-C", cwd, "log", branch,
                "--format=%H|%an|%ae|%ai|%s",
                f"--after={after.isoformat()}",
                f"--before={before.isoformat()}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"[git-reconcile] git error in {cwd!r}: {exc}", file=sys.stderr)
        return []

    if result.returncode != 0:
        print(
            f"[git-reconcile] git log failed in {cwd!r}: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return []

    commits = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        sha, author_name, author_email, date_str, message = parts
        try:
            committed_at: datetime | None = datetime.fromisoformat(date_str.strip())
        except ValueError:
            committed_at = None
        commits.append(
            {
                "commit_sha": sha.strip(),
                "author_name": author_name.strip() or None,
                "author_email": author_email.strip() or None,
                "committed_at": committed_at,
                "commit_message": message.strip() or None,
            }
        )
    return commits
