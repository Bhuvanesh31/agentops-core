"""Tests for capture.proof_export — safety guarantees and structural correctness."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.database import get_connection
from app.models import ingestion
from capture.proof_export import _EXPORT_SAFETY, build_proof_summary
from capture.git.reconcile import upsert_commits
from tests.conftest import SEED_PROJECT_ID, SEED_REPOSITORY_ID, SEED_TOOL_ID


# ── Helpers ───────────────────────────────────────────────────────────────────

def _seed_run(session_id, *, ended=True):
    with get_connection() as conn:
        run_id, _ = ingestion.get_or_create_run(
            conn,
            project_id=SEED_PROJECT_ID,
            repository_id=SEED_REPOSITORY_ID,
            tool_id=SEED_TOOL_ID,
            session_id=session_id,
            model="claude-opus-4-8",
            branch="main",
            cwd="/tmp/work",
            intent=None,
        )
        if ended:
            conn.execute(
                "UPDATE runs SET ended_at = NOW(), status = 'completed' WHERE run_id = %s",
                (run_id,),
            )
    return str(run_id)


def _seed_commit(run_id, subject="test: proof commit"):
    sha = f"pytest-sha-{uuid4().hex[:16]}"
    commit = {
        "commit_sha": sha,
        "author_name": "Tester",
        "author_email": "t@example.com",
        "commit_message": subject,
        "committed_at": datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
    }
    with get_connection() as conn:
        upsert_commits(conn, run_id, SEED_REPOSITORY_ID, "main", [commit])
    return sha


def _contains_key_anywhere(obj, key: str) -> bool:
    """Recursively check if any dict anywhere in obj contains 'key'."""
    if isinstance(obj, dict):
        if key in obj:
            return True
        return any(_contains_key_anywhere(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_key_anywhere(item, key) for item in obj)
    return False


# ── Safety tests ──────────────────────────────────────────────────────────────

def test_raw_payloads_not_included(client):
    sid = f"pytest-session-{uuid4().hex}"
    _seed_run(sid)
    with get_connection() as conn:
        summary = build_proof_summary(conn)
    assert not _contains_key_anywhere(summary, "raw_payload"), (
        "raw_payload must never appear in proof export"
    )
    assert summary["export_safety"]["raw_payloads_included"] is False


def test_prompts_not_included(client):
    sid = f"pytest-session-{uuid4().hex}"
    _seed_run(sid)
    with get_connection() as conn:
        summary = build_proof_summary(conn)
    for sensitive_key in ("prompt", "transcript", "diff", "file_content", "secret"):
        assert not _contains_key_anywhere(summary, sensitive_key), (
            f"sensitive key '{sensitive_key}' must not appear in proof export"
        )
    assert summary["export_safety"]["prompts_included"] is False
    assert summary["export_safety"]["transcripts_included"] is False
    assert summary["export_safety"]["diffs_included"] is False
    assert summary["export_safety"]["file_contents_included"] is False
    assert summary["export_safety"]["secrets_included"] is False


# ── Structural tests ──────────────────────────────────────────────────────────

def test_export_safety_block_hardcoded(client):
    with get_connection() as conn:
        summary = build_proof_summary(conn)
    assert summary["export_safety"] == _EXPORT_SAFETY
    assert all(v is False for v in _EXPORT_SAFETY.values())


def test_schema_keys_always_present(client):
    with get_connection() as conn:
        summary = build_proof_summary(conn)
    for key in (
        "as_of", "schema_version", "total_runs", "total_events",
        "total_tokens", "total_repositories_observed",
        "repositories", "recent_proof_events",
        "reconciliation_health", "export_safety",
    ):
        assert key in summary, f"missing top-level key: {key}"
    for key in ("runs_with_commits", "runs_without_commits", "next_action"):
        assert key in summary["reconciliation_health"], f"missing health key: {key}"


# ── Content tests ─────────────────────────────────────────────────────────────

def test_no_commits_case(client):
    sid = f"pytest-session-{uuid4().hex}"
    _seed_run(sid)
    with get_connection() as conn:
        summary = build_proof_summary(conn)
    # Our test run has no commits — it should not appear in recent_proof_events
    # with non-zero linked_commit_count, but it may appear with 0.
    for event in summary["recent_proof_events"]:
        subjects = event.get("commit_subjects", [])
        assert isinstance(subjects, list)


def test_partial_reconciliation_coverage(client):
    sid_with = f"pytest-session-{uuid4().hex}"
    sid_without = f"pytest-session-{uuid4().hex}"
    run_with = _seed_run(sid_with)
    _seed_run(sid_without)
    _seed_commit(run_with, "feat: partial coverage test")

    with get_connection() as conn:
        summary = build_proof_summary(conn)

    health = summary["reconciliation_health"]
    # At least one run with commits, at least one without (our seeds above).
    assert health["runs_with_commits"] >= 1
    assert health["runs_without_commits"] >= 1

    # The repository entry for SEED_REPOSITORY_ID has reconciliation_coverage in [0, 1].
    repo_entry = next(
        (r for r in summary["repositories"] if r["repository_id"] == SEED_REPOSITORY_ID),
        None,
    )
    assert repo_entry is not None
    assert 0.0 <= repo_entry["reconciliation_coverage"] <= 1.0


def test_commit_subjects_present_for_run_with_commits(client):
    sid = f"pytest-session-{uuid4().hex}"
    run_id = _seed_run(sid)
    subject = f"feat: proof subject {uuid4().hex[:8]}"
    sha = _seed_commit(run_id, subject)

    with get_connection() as conn:
        summary = build_proof_summary(conn)

    # Find the event entry for our run.
    match = next(
        (e for e in summary["recent_proof_events"] if e["run_id"] == run_id),
        None,
    )
    assert match is not None, "run not found in recent_proof_events"
    assert match["linked_commit_count"] >= 1
    assert subject in match["commit_subjects"]
    # Double-check the commit SHA does NOT appear (subjects only, not full commit objects).
    assert sha not in json.dumps(match)


# ── CLI test ──────────────────────────────────────────────────────────────────

def test_cli_produces_valid_json():
    result = subprocess.run(
        [sys.executable, "-m", "capture.proof_export"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"CLI exited non-zero: {result.stderr}"
    parsed = json.loads(result.stdout)
    assert parsed["schema_version"] == "1"
    assert "export_safety" in parsed
    assert parsed["export_safety"]["raw_payloads_included"] is False


# ── API test ──────────────────────────────────────────────────────────────────

def test_api_proof_summary_endpoint(client):
    resp = client.get("/proof-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "1"
    assert body["export_safety"]["prompts_included"] is False
    assert isinstance(body["repositories"], list)
    assert isinstance(body["recent_proof_events"], list)
