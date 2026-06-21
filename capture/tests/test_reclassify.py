from uuid import uuid4

from app.database import get_connection
from app.models import ingestion
from capture.claude_code.reclassify import reclassify


def _seed_unsorted_run(conn, session_id, cwd):
    ingestion.get_or_create_run(
        conn,
        project_id="unsorted",
        repository_id="unsorted-local",
        tool_id="claude-code",
        session_id=session_id,
        model="claude-opus-4-8",
        branch="main",
        cwd=cwd,
        intent=None,
    )


def test_reclassify_moves_mapped_runs_only():
    repo_id = f"pytest-repo-{uuid4().hex}"
    mapped_cwd = f"/pytest/mapped/{uuid4().hex}"
    unmapped_cwd = f"/pytest/unmapped/{uuid4().hex}"
    mapped_session = f"pytest-session-{uuid4().hex}"
    child_session = f"pytest-session-{uuid4().hex}"
    unmapped_session = f"pytest-session-{uuid4().hex}"

    with get_connection() as conn:
        ingestion.upsert_repository(
            conn,
            repository_id=repo_id,
            project_id="agentops-core",
            repository_name="Reclass Target",
            remote_url=None,
            local_path=None,
            default_branch="main",
            is_active=True,
        )
        _seed_unsorted_run(conn, mapped_session, mapped_cwd)
        _seed_unsorted_run(conn, child_session, f"{mapped_cwd}/sub/dir")
        _seed_unsorted_run(conn, unmapped_session, unmapped_cwd)

    with get_connection() as conn:
        moved = reclassify(conn, {mapped_cwd: repo_id})

    assert moved[repo_id] == 2  # exact + child path

    with get_connection() as conn:
        mapped = conn.execute(
            "SELECT repository_id, project_id FROM runs WHERE session_id = %s",
            (mapped_session,),
        ).fetchone()
        unmapped = conn.execute(
            "SELECT repository_id FROM runs WHERE session_id = %s",
            (unmapped_session,),
        ).fetchone()
    assert mapped["repository_id"] == repo_id
    assert mapped["project_id"] == "agentops-core"
    assert unmapped["repository_id"] == "unsorted-local"  # untouched


def test_reclassify_skips_unregistered_repo():
    cwd = f"/pytest/x/{uuid4().hex}"
    session_id = f"pytest-session-{uuid4().hex}"
    with get_connection() as conn:
        _seed_unsorted_run(conn, session_id, cwd)
    with get_connection() as conn:
        moved = reclassify(conn, {cwd: "pytest-not-registered"})
    assert moved == {}  # repo not registered -> skipped
    with get_connection() as conn:
        run = conn.execute(
            "SELECT repository_id FROM runs WHERE session_id = %s", (session_id,)
        ).fetchone()
    assert run["repository_id"] == "unsorted-local"


def test_reclassify_is_idempotent():
    repo_id = f"pytest-repo-{uuid4().hex}"
    cwd = f"/pytest/idem/{uuid4().hex}"
    session_id = f"pytest-session-{uuid4().hex}"
    with get_connection() as conn:
        ingestion.upsert_repository(
            conn,
            repository_id=repo_id,
            project_id="agentops-core",
            repository_name="Idem",
            remote_url=None,
            local_path=None,
            default_branch="main",
            is_active=True,
        )
        _seed_unsorted_run(conn, session_id, cwd)
    with get_connection() as conn:
        first = reclassify(conn, {cwd: repo_id})
    with get_connection() as conn:
        second = reclassify(conn, {cwd: repo_id})
    assert first[repo_id] == 1
    assert second.get(repo_id, 0) == 0


def test_reclassify_underscore_does_not_overmatch_sibling():
    """Underscore in a mapped cwd must not act as a LIKE wildcard for sibling paths."""
    repo_id = f"pytest-repo-{uuid4().hex}"
    mapped_cwd = f"/pytest/leadle_x/{uuid4().hex}"
    child_session = f"pytest-session-{uuid4().hex}"
    sibling_session = f"pytest-session-{uuid4().hex}"

    with get_connection() as conn:
        ingestion.upsert_repository(
            conn,
            repository_id=repo_id,
            project_id="agentops-core",
            repository_name="Underscore Target",
            remote_url=None,
            local_path=None,
            default_branch="main",
            is_active=True,
        )
        # Child path under the mapped cwd — should move.
        _seed_unsorted_run(conn, child_session, f"{mapped_cwd}/sub")
        # Sibling whose path differs only by replacing `_` with `Z` — must NOT move.
        sibling_cwd = mapped_cwd.replace("_", "Z") + "/sub"
        _seed_unsorted_run(conn, sibling_session, sibling_cwd)

    with get_connection() as conn:
        moved = reclassify(conn, {mapped_cwd: repo_id})

    assert moved[repo_id] == 1  # only the child moved

    with get_connection() as conn:
        child = conn.execute(
            "SELECT repository_id FROM runs WHERE session_id = %s", (child_session,)
        ).fetchone()
        sibling = conn.execute(
            "SELECT repository_id FROM runs WHERE session_id = %s", (sibling_session,)
        ).fetchone()
    assert child["repository_id"] == repo_id
    assert sibling["repository_id"] == "unsorted-local"  # untouched


def test_reclassify_empty_map_is_noop():
    """reclassify({}) returns {} and leaves existing runs untouched."""
    session_id = f"pytest-session-{uuid4().hex}"
    cwd = f"/pytest/noop/{uuid4().hex}"

    with get_connection() as conn:
        _seed_unsorted_run(conn, session_id, cwd)

    with get_connection() as conn:
        moved = reclassify(conn, {})

    assert moved == {}

    with get_connection() as conn:
        run = conn.execute(
            "SELECT repository_id FROM runs WHERE session_id = %s", (session_id,)
        ).fetchone()
    assert run["repository_id"] == "unsorted-local"
