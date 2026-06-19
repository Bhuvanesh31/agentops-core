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
        assert run["repository_id"] == "agentops-core-main"
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

    with get_connection() as conn:
        run = conn.execute(
            "SELECT run_id FROM runs WHERE session_id = %s", (session_id,)
        ).fetchone()
    assert run is None
