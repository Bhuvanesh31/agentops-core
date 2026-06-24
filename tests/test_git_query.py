"""Tests for capture.git.query — git log wrapper."""

import os
import subprocess
from datetime import datetime, timezone

import pytest

from capture.git.query import query_commits


@pytest.fixture
def git_repo(tmp_path):
    """Real git repo with 2 commits at controlled timestamps."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def run(*args, **extra_env):
        env = {**os.environ, **extra_env}
        subprocess.run(list(args), cwd=str(repo), check=True, env=env, capture_output=True)

    run("git", "init")
    run("git", "config", "user.email", "t@t.com")
    run("git", "config", "user.name", "Tester")
    run("git", "checkout", "-b", "main")

    (repo / "a.txt").write_text("a")
    run("git", "add", ".")
    run(
        "git", "commit", "-m", "first commit",
        GIT_AUTHOR_DATE="2026-06-25T10:00:00+00:00",
        GIT_COMMITTER_DATE="2026-06-25T10:00:00+00:00",
    )

    (repo / "b.txt").write_text("b")
    run("git", "add", ".")
    run(
        "git", "commit", "-m", "second commit",
        GIT_AUTHOR_DATE="2026-06-25T10:30:00+00:00",
        GIT_COMMITTER_DATE="2026-06-25T10:30:00+00:00",
    )

    return repo


def test_query_commits_returns_all_in_window(git_repo):
    after = datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc)
    before = datetime(2026, 6, 25, 11, 0, tzinfo=timezone.utc)
    commits = query_commits(str(git_repo), "main", after, before)
    assert len(commits) == 2
    messages = {c["commit_message"] for c in commits}
    assert messages == {"first commit", "second commit"}
    sample = commits[0]
    assert sample["author_name"] == "Tester"
    assert sample["author_email"] == "t@t.com"
    assert sample["committed_at"] is not None
    assert len(sample["commit_sha"]) == 40


def test_query_commits_window_excludes_later_commit(git_repo):
    # Window ends at 10:15 — only first commit (10:00) is inside.
    after = datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc)
    before = datetime(2026, 6, 25, 10, 15, tzinfo=timezone.utc)
    commits = query_commits(str(git_repo), "main", after, before)
    assert len(commits) == 1
    assert commits[0]["commit_message"] == "first commit"


def test_query_commits_nonexistent_cwd_returns_empty():
    after = datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc)
    before = datetime(2026, 6, 25, 11, 0, tzinfo=timezone.utc)
    result = query_commits("/nonexistent/path/xyz", "main", after, before)
    assert result == []


def test_query_commits_nonexistent_branch_returns_empty(git_repo):
    after = datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc)
    before = datetime(2026, 6, 25, 11, 0, tzinfo=timezone.utc)
    result = query_commits(str(git_repo), "no-such-branch", after, before)
    assert result == []
