import json
import subprocess
from uuid import uuid4

from app.database import get_connection
from capture.claude_code import cli


def _repo_root() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _write_transcript(tmp_path, session_id):
    # cwd is THIS repo, whose origin matches the seeded repository remote.
    cwd = _repo_root()
    slug = "-".join(cwd.strip("/").split("/"))
    d = tmp_path / slug
    d.mkdir(parents=True)
    base = {"sessionId": session_id, "cwd": cwd, "gitBranch": "main"}
    lines = [
        {
            **base,
            "type": "user",
            "uuid": f"pytest-{session_id}-u1",
            "timestamp": "2026-06-19T00:00:00Z",
            "message": {"content": "do the thing"},
        },
        {
            **base,
            "type": "assistant",
            "uuid": f"pytest-{session_id}-a1",
            "timestamp": "2026-06-19T00:00:01Z",
            "message": {
                "model": "claude-opus-4-8",
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"pytest-{session_id}-t1",
                        "name": "Edit",
                        "input": {"file_path": f"{cwd}/x.py"},
                    }
                ],
            },
        },
    ]
    f = d / f"{session_id}.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return f


def test_e2e_ingests_and_is_idempotent(asgi_client, tmp_path):
    session_id = f"pytest-session-{uuid4().hex}"
    _write_transcript(tmp_path, session_id)

    from capture.claude_code.discovery import discover_transcripts

    files = discover_transcripts(str(tmp_path))
    report1 = cli.process(asgi_client, "http://test", files, now=10_000_000_000.0)

    assert (
        report1.events_created >= 3
    )  # session_started + user_prompt + assistant_message + file_edit
    assert report1.events_error == 0

    with get_connection() as conn:
        run = conn.execute(
            "SELECT run_id, repository_id, project_id, model FROM runs WHERE session_id = %s",
            (session_id,),
        ).fetchone()
        assert run is not None
        assert run["repository_id"] in {"agentops-core-main", "agentops-core-fork"}
        assert run["project_id"] == "agentops-core"
        assert run["model"] == "claude-opus-4-8"
        count1 = conn.execute(
            "SELECT count(*) AS c FROM run_events WHERE session_id = %s", (session_id,)
        ).fetchone()["c"]

    # Re-run: everything is a duplicate, no new rows.
    report2 = cli.process(asgi_client, "http://test", files, now=10_000_000_000.0)
    assert report2.events_created == 0
    assert report2.events_duplicate >= 3

    with get_connection() as conn:
        count2 = conn.execute(
            "SELECT count(*) AS c FROM run_events WHERE session_id = %s", (session_id,)
        ).fetchone()["c"]
    assert count2 == count1


def test_e2e_dry_run_writes_nothing(asgi_client, tmp_path):
    session_id = f"pytest-session-{uuid4().hex}"
    _write_transcript(tmp_path, session_id)
    from capture.claude_code.discovery import discover_transcripts

    files = discover_transcripts(str(tmp_path))
    report = cli.process(asgi_client, "http://test", files, dry_run=True, now=10_000_000_000.0)
    assert report.events_created == 0
    assert report.events_duplicate == 0
    assert report.events_error == 0

    with get_connection() as conn:
        run = conn.execute(
            "SELECT run_id FROM runs WHERE session_id = %s", (session_id,)
        ).fetchone()
    assert run is None


def test_process_counts_post_errors_without_aborting(asgi_client, tmp_path, monkeypatch):
    session_id = f"pytest-session-{uuid4().hex}"
    _write_transcript(tmp_path, session_id)

    from capture.claude_code import client as client_mod
    from capture.claude_code.discovery import discover_transcripts

    def boom(*args, **kwargs):
        raise RuntimeError("simulated exhausted retries")

    monkeypatch.setattr(client_mod, "post_event", boom)

    files = discover_transcripts(str(tmp_path))
    report = cli.process(asgi_client, "http://test", files, now=10_000_000_000.0)

    assert report.events_error >= 1
    assert report.events_created == 0


def _write_local_transcript(tmp_path, cwd, session_id):
    # filename stem == session_id so synthesized and normalized events agree.
    d = tmp_path / "proj-slug"
    d.mkdir(parents=True, exist_ok=True)
    base = {"sessionId": session_id, "cwd": cwd, "gitBranch": "main"}
    lines = [
        {
            **base,
            "type": "user",
            "uuid": f"{session_id}-u1",
            "timestamp": "2026-06-19T00:00:00Z",
            "message": {"content": "hi"},
        },
        {
            **base,
            "type": "assistant",
            "uuid": f"{session_id}-a1",
            "timestamp": "2026-06-19T00:00:01Z",
            "message": {"model": "claude-opus-4-8", "content": [{"type": "text", "text": "ok"}]},
        },
    ]
    f = d / f"{session_id}.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return f


def test_local_only_routed_to_catch_all(asgi_client, tmp_path):
    # cwd is a plain temp dir (not a git repo) -> resolves local_only -> catch-all.
    session_id = f"pytest-session-{uuid4().hex}"
    cwd = str(tmp_path)
    _write_local_transcript(tmp_path, cwd, session_id)

    from capture.claude_code.discovery import discover_transcripts

    files = discover_transcripts(str(tmp_path))
    report = cli.process(
        asgi_client, "http://test", files, now=10_000_000_000.0, catch_all="unsorted-local"
    )
    assert report.files_catch_all == 1
    assert report.files_processed == 0

    with get_connection() as conn:
        run = conn.execute(
            "SELECT repository_id, project_id, cwd FROM runs WHERE session_id = %s",
            (session_id,),
        ).fetchone()
    assert run is not None
    assert run["repository_id"] == "unsorted-local"
    assert run["project_id"] == "unsorted"
    assert run["cwd"] == cwd  # real folder preserved for later reclassification


def test_excluded_cwd_is_skipped(asgi_client, tmp_path):
    session_id = f"pytest-session-{uuid4().hex}"
    cwd = f"{tmp_path}/personal-notes"
    _write_local_transcript(tmp_path, cwd, session_id)

    from capture.claude_code.discovery import discover_transcripts

    files = discover_transcripts(str(tmp_path))
    report = cli.process(
        asgi_client,
        "http://test",
        files,
        now=10_000_000_000.0,
        catch_all="unsorted-local",
        exclude=["personal"],
    )
    assert any("personal" in k for k in report.excluded)
    assert report.files_catch_all == 0

    with get_connection() as conn:
        run = conn.execute("SELECT 1 FROM runs WHERE session_id = %s", (session_id,)).fetchone()
    assert run is None


def test_catch_all_unregistered_repo_raises(asgi_client):
    import pytest

    with pytest.raises(ValueError):
        cli.process(asgi_client, "http://test", [], catch_all="does-not-exist")


def test_canonical_cwd_injected_when_first_line_lacks_it(tmp_path):
    # Regression: real transcripts can start with a metadata line that has no
    # cwd; the run is created by session_started, so without injection it would
    # land with a null cwd and lose the catch-all reclassification signal.
    from capture.claude_code import identity

    d = tmp_path / "slug"
    d.mkdir()
    session_id = f"pytest-session-{uuid4().hex}"
    lines = [
        {"type": "summary", "sessionId": session_id, "timestamp": "2026-06-19T00:00:00Z"},
        {
            "type": "user",
            "sessionId": session_id,
            "cwd": "/home/me/notes",
            "gitBranch": "main",
            "uuid": f"{session_id}-u1",
            "timestamp": "2026-06-19T00:00:01Z",
            "message": {"content": "hi"},
        },
    ]
    path = d / f"{session_id}.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")

    resolution = identity.RepoResolution("registered", None, "unsorted", "unsorted-local")
    events = cli.events_for_file(path, lines, resolution, now=10_000_000_000.0)

    assert events  # at least session_started + user_prompt (+ session_ended)
    assert all(e["cwd"] == "/home/me/notes" for e in events)
    started = next(e for e in events if e["event_type"] == "session_started")
    assert started["cwd"] == "/home/me/notes"


def test_events_for_file_subagent_skips_boundaries_and_uses_parent_session(tmp_path):
    from capture.claude_code import identity

    parent = "11111111-1111-1111-1111-111111111111"
    d = tmp_path / "slug" / parent / "subagents"
    d.mkdir(parents=True)
    path = d / "agent-abc.jsonl"
    line = {
        "type": "assistant",
        "sessionId": parent,
        "agentId": "agentX",
        "isSidechain": True,
        "cwd": "/repo",
        "gitBranch": "main",
        "uuid": "sa1",
        "timestamp": "2026-06-19T00:00:00Z",
        "message": {"model": "claude-opus-4-8", "content": [{"type": "text", "text": "hi"}]},
    }
    path.write_text(json.dumps(line) + "\n")

    resolution = identity.RepoResolution("registered", None, "p", "r")
    events = cli.events_for_file(path, [line], resolution, now=10_000_000_000.0)

    types = [e["event_type"] for e in events]
    assert "session_started" not in types
    assert "session_ended" not in types
    assert events  # the assistant_message is present
    assert all(e["session_id"] == parent for e in events)


def test_events_for_file_subagent_uses_path_session_when_line_lacks_session_id(tmp_path):
    """Sub-agent events must use the path-derived parent session id even when
    the line omits sessionId entirely (§5.2 robustness requirement)."""
    from capture.claude_code import identity

    parent = "22222222-2222-2222-2222-222222222222"
    d = tmp_path / "slug" / parent / "subagents"
    d.mkdir(parents=True)
    path = d / "agent-y.jsonl"
    # Deliberately omit sessionId — the fix must still yield the correct session_id.
    line = {
        "type": "assistant",
        "agentId": "agentY",
        "isSidechain": True,
        "cwd": "/repo",
        "gitBranch": "main",
        "uuid": "sa2",
        "timestamp": "2026-06-19T00:00:00Z",
        "message": {"model": "claude-opus-4-8", "content": [{"type": "text", "text": "hello"}]},
    }
    path.write_text(json.dumps(line) + "\n")

    resolution = identity.RepoResolution("registered", None, "p", "r")
    events = cli.events_for_file(path, [line], resolution, now=10_000_000_000.0)

    assert events, "expected at least one event (assistant_message)"
    assert all(e["session_id"] == parent for e in events), (
        f"expected all session_ids == {parent!r}, got {[e['session_id'] for e in events]}"
    )


def test_subagent_events_merge_into_parent_run(asgi_client, tmp_path):
    cwd = _repo_root()  # origin matches the seeded agentops-core-main repo
    parent = f"pytest-session-{uuid4().hex}"
    slug = "-".join(cwd.strip("/").split("/"))

    pdir = tmp_path / slug
    pdir.mkdir(parents=True)
    parent_lines = [
        {
            "type": "user",
            "sessionId": parent,
            "cwd": cwd,
            "gitBranch": "main",
            "uuid": f"{parent}-u",
            "timestamp": "2026-06-19T00:00:00Z",
            "message": {"content": "go"},
        },
        {
            "type": "assistant",
            "sessionId": parent,
            "cwd": cwd,
            "gitBranch": "main",
            "uuid": f"{parent}-a",
            "timestamp": "2026-06-19T00:00:01Z",
            "message": {"model": "claude-opus-4-8", "content": [{"type": "text", "text": "ok"}]},
        },
    ]
    (pdir / f"{parent}.jsonl").write_text("\n".join(json.dumps(x) for x in parent_lines) + "\n")

    sdir = pdir / parent / "subagents"
    sdir.mkdir(parents=True)
    sub_line = {
        "type": "assistant",
        "sessionId": parent,
        "agentId": "agentX",
        "isSidechain": True,
        "attributionAgent": "Explore",
        "cwd": cwd,
        "gitBranch": "main",
        "uuid": f"{parent}-sa",
        "timestamp": "2026-06-19T00:00:02Z",
        "message": {"model": "claude-opus-4-8", "content": [{"type": "text", "text": "searched"}]},
    }
    (sdir / "agent-x.jsonl").write_text(json.dumps(sub_line) + "\n")

    from capture.claude_code.discovery import discover_transcripts

    files = discover_transcripts(str(tmp_path))
    report = cli.process(asgi_client, "http://test", files, now=10_000_000_000.0)

    assert report.sub_agent_files == 1
    assert report.events_error == 0

    with get_connection() as conn:
        runs = conn.execute("SELECT run_id FROM runs WHERE session_id = %s", (parent,)).fetchall()
        assert len(runs) == 1  # parent + sub-agent share ONE run
        sidechain = conn.execute(
            "SELECT count(*) AS c FROM run_events e JOIN runs r ON e.run_id = r.run_id "
            "WHERE r.session_id = %s AND e.raw_payload->>'isSidechain' = 'true'",
            (parent,),
        ).fetchone()["c"]
        assert sidechain >= 1

    # Idempotent re-run.
    report2 = cli.process(asgi_client, "http://test", files, now=10_000_000_000.0)
    assert report2.events_created == 0


def test_no_remote_cwd_routed_via_override_map(asgi_client, tmp_path):
    # Register a real repo, then map a no-remote cwd to it via cwd_overrides.
    import os

    from capture.claude_code.discovery import discover_transcripts

    repo_id = f"pytest-repo-{uuid4().hex}"
    resp = asgi_client.post(
        "/repositories",
        json={
            "repository_id": repo_id,
            "project_id": "agentops-core",
            "repository_name": "Override Target",
        },
    )
    assert resp.status_code in (200, 201)

    session_id = f"pytest-session-{uuid4().hex}"
    cwd = str(tmp_path / "mapped-folder")
    os.makedirs(cwd, exist_ok=True)
    _write_local_transcript(tmp_path, cwd, session_id)

    files = discover_transcripts(str(tmp_path))
    report = cli.process(
        asgi_client,
        "http://test",
        files,
        now=10_000_000_000.0,
        cwd_overrides={cwd: repo_id},
    )
    assert report.files_processed == 1
    assert report.files_catch_all == 0

    with get_connection() as conn:
        run = conn.execute(
            "SELECT repository_id, project_id FROM runs WHERE session_id = %s",
            (session_id,),
        ).fetchone()
    assert run is not None
    assert run["repository_id"] == repo_id
    assert run["project_id"] == "agentops-core"
