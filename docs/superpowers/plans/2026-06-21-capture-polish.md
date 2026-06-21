# Capture Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give captured sessions a real project/repository home: add a `POST /repositories` write endpoint and reclassify the 78 catch-all (`unsorted`) sessions into their real repos durably, via a shared `cwd → repository_id` override map.

**Architecture:** A new write endpoint upserts repositories (validating the project FK). A version-controlled TOML map (`cwd → repository_id`) is consumed by **both** the capture adapter (so no-remote transcripts resolve to real repos instead of the catch-all) and a host-local reclassify command (which re-points the already-stored runs via direct `UPDATE`). Reference data (3 projects + 4 repos) is seeded for fresh-DB reproducibility. No event rewrite — `run_events` hang off `run_id`.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 (raw SQL, no ORM), pydantic, `tomllib` (stdlib), httpx, pytest, ruff.

## Global Constraints

- Python `>=3.12`; psycopg 3 raw parameterized SQL, no ORM; reuse `app.redaction`; tool slug `"claude-code"`.
- ruff: `line-length = 100`, lint select `E, F, I, UP, B`.
- Tests run against the live Postgres container; create rows with ids prefixed `pytest-`; sanitized synthetic data only.
- `get_connection()` commits on clean exit, rolls back on exception (one transaction per `with` block).
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Branch: `feat/capture-polish`.
- **Reference data (verbatim):**
  - Projects: `leadle`, `ai-native-work-brain`, `ai-work-journal`.
  - Repositories (`repository_id` → `project_id`, `remote_url`):
    - `leadle-os` → `leadle`, `https://github.com/Bhuvanesh31/leadle-os.git`
    - `leadle-content-studio` → `leadle`, `https://github.com/Bhuvanesh31/leadle-content-studio.git`
    - `ai-native-revops-work-brain` → `ai-native-work-brain`, `https://github.com/Bhuvanesh31/ai-native-revops-work-brain.git`
    - `ai-work-journal` → `ai-work-journal`, `https://github.com/Bhuvanesh31/ai-work-journal.git`
  - Override map (`cwd → repository_id`):
    - `/home/bhuvanesh/leadle_master_claude` → `leadle-os`
    - `/home/bhuvanesh/AI_Native_Workspace/30-leadle-systems/leadle_gtm_intelligence` → `leadle-os`
    - `/home/bhuvanesh/AI_Native_Workspace/30-leadle-systems/leadle_content_studio` → `leadle-content-studio`
    - `/home/bhuvanesh/Claude Folders/AI_Native_Plans` → `ai-native-revops-work-brain`
    - `/home/bhuvanesh/Claude Folders/claude_sessions_project` → `ai-work-journal`
- Catch-all repository id is `unsorted-local` (project `unsorted`).

---

### Task 1: `POST /repositories` write endpoint

**Files:**
- Modify: `app/schemas/repositories.py`
- Modify: `app/models/ingestion.py`
- Modify: `app/routes/repositories.py`
- Modify: `tests/conftest.py` (extend cleanup to drop `pytest-` repositories)
- Test: `tests/test_repositories.py`

**Interfaces:**
- Produces:
  - `RepositoryCreate` pydantic model: `repository_id: str, project_id: str, repository_name: str, remote_url: str | None = None, local_path: str | None = None, default_branch: str = "main", is_active: bool = True`.
  - `ingestion.upsert_repository(conn, *, repository_id, project_id, repository_name, remote_url, local_path, default_branch, is_active) -> dict` (returns `{repository_id, project_id, remote_url}`).
  - `POST /repositories` → `201` (created) / `200` (idempotent update) returning `RepositoryOut`; `400` if `project_id` does not exist.

- [ ] **Step 1: Extend the test-cleanup fixture to remove pytest repositories**

In `tests/conftest.py`, the `cleanup_pytest_rows` fixture currently deletes `runs` and `run_events`. Add a repositories cleanup line so created test repos don't leak. Replace the fixture body's delete block:

```python
@pytest.fixture(autouse=True)
def cleanup_pytest_rows() -> Iterator[None]:
    yield
    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM runs WHERE session_id LIKE 'pytest-%'")
            conn.execute("DELETE FROM run_events WHERE source_event_id LIKE 'pytest-%'")
            conn.execute("DELETE FROM repositories WHERE repository_id LIKE 'pytest-%'")
    except Exception:
        pass
```

(If the existing fixture body differs, keep its existing two deletes and add only the `repositories` delete line.)

- [ ] **Step 2: Write the failing endpoint tests**

```python
# tests/test_repositories.py — append

def test_create_repository_creates_row(client):
    body = {
        "repository_id": "pytest-repo-create",
        "project_id": "agentops-core",
        "repository_name": "Pytest Repo",
        "remote_url": "https://github.com/acme/pytest-repo.git",
    }
    resp = client.post("/repositories", json=body)
    assert resp.status_code == 201
    out = resp.json()
    assert out["repository_id"] == "pytest-repo-create"
    assert out["project_id"] == "agentops-core"
    listed = {r["repository_id"] for r in client.get("/repositories").json()}
    assert "pytest-repo-create" in listed


def test_create_repository_unknown_project_is_400(client):
    body = {
        "repository_id": "pytest-repo-badproj",
        "project_id": "does-not-exist",
        "repository_name": "X",
    }
    resp = client.post("/repositories", json=body)
    assert resp.status_code == 400


def test_create_repository_idempotent_upsert(client):
    body = {
        "repository_id": "pytest-repo-upsert",
        "project_id": "agentops-core",
        "repository_name": "First Name",
    }
    first = client.post("/repositories", json=body)
    assert first.status_code == 201
    body["repository_name"] = "Second Name"
    second = client.post("/repositories", json=body)
    assert second.status_code == 200
    rows = [r for r in client.get("/repositories").json() if r["repository_id"] == "pytest-repo-upsert"]
    assert len(rows) == 1
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_repositories.py -v`
Expected: FAIL — `POST /repositories` returns 405 (no handler) / `RepositoryCreate` undefined.

- [ ] **Step 4: Add the `RepositoryCreate` schema**

```python
# app/schemas/repositories.py — append (keep existing RepositoryOut)
class RepositoryCreate(BaseModel):
    repository_id: str
    project_id: str
    repository_name: str
    remote_url: str | None = None
    local_path: str | None = None
    default_branch: str = "main"
    is_active: bool = True
```

- [ ] **Step 5: Add `upsert_repository` to the model layer**

```python
# app/models/ingestion.py — append
def upsert_repository(
    conn: psycopg.Connection,
    *,
    repository_id: str,
    project_id: str,
    repository_name: str,
    remote_url: str | None,
    local_path: str | None,
    default_branch: str,
    is_active: bool,
) -> dict[str, Any]:
    """Insert or update a repository; return {repository_id, project_id, remote_url}."""
    return conn.execute(
        """
        INSERT INTO repositories (
            repository_id, project_id, repository_name,
            remote_url, local_path, default_branch, is_active
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (repository_id) DO UPDATE SET
            project_id      = EXCLUDED.project_id,
            repository_name = EXCLUDED.repository_name,
            remote_url      = EXCLUDED.remote_url,
            local_path      = EXCLUDED.local_path,
            default_branch  = EXCLUDED.default_branch,
            is_active       = EXCLUDED.is_active,
            updated_at      = NOW()
        RETURNING repository_id, project_id, remote_url
        """,
        (
            repository_id,
            project_id,
            repository_name,
            remote_url,
            local_path,
            default_branch,
            is_active,
        ),
    ).fetchone()
```

- [ ] **Step 6: Add the POST route**

```python
# app/routes/repositories.py — replace the file's import block + add the handler
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.database import db_dependency
from app.models import ingestion
from app.schemas.repositories import RepositoryCreate, RepositoryOut

router = APIRouter(tags=["repositories"])


@router.get("/repositories", response_model=list[RepositoryOut])
def list_repositories(conn: psycopg.Connection = Depends(db_dependency)) -> list[dict]:
    return ingestion.list_repositories(conn)


@router.post("/repositories", response_model=RepositoryOut, status_code=status.HTTP_201_CREATED)
def create_repository(
    repo: RepositoryCreate,
    response: Response,
    conn: psycopg.Connection = Depends(db_dependency),
) -> dict:
    if ingestion.get_project(conn, repo.project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown project_id: {repo.project_id}",
        )
    existed = ingestion.get_repository(conn, repo.repository_id) is not None
    row = ingestion.upsert_repository(conn, **repo.model_dump())
    response.status_code = status.HTTP_200_OK if existed else status.HTTP_201_CREATED
    return row
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_repositories.py -v`
Expected: PASS (4 tests: the existing list test + 3 new).

- [ ] **Step 8: Lint and commit**

```bash
.venv/bin/ruff format app tests
.venv/bin/ruff check app tests
git add app/schemas/repositories.py app/models/ingestion.py app/routes/repositories.py tests/conftest.py tests/test_repositories.py
git commit -m "feat: POST /repositories write endpoint with idempotent upsert

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: cwd override map — loader + matcher + file

**Files:**
- Create: `capture/claude_code/overrides.py`
- Create: `capture/claude_code/cwd_overrides.toml`
- Test: `capture/tests/test_overrides.py`

**Interfaces:**
- Produces:
  - `DEFAULT_OVERRIDES_PATH: Path` — the bundled `cwd_overrides.toml`.
  - `load_overrides(path: Path | str | None = None) -> dict[str, str]` — parse the `[overrides]` table; missing file → `{}`.
  - `match_override(cwd: str, mapping: dict[str, V]) -> V | None` — longest-prefix match (`cwd == key` or `cwd.startswith(key + "/")`); returns the value of the longest matching key (the value type is whatever the mapping holds), else `None`.

- [ ] **Step 1: Write the failing tests**

```python
# capture/tests/test_overrides.py
from capture.claude_code.overrides import load_overrides, match_override


def test_match_override_exact():
    m = {"/a/b": "repo1"}
    assert match_override("/a/b", m) == "repo1"


def test_match_override_child_path():
    m = {"/a/b": "repo1"}
    assert match_override("/a/b/sub/dir", m) == "repo1"


def test_match_override_longest_prefix_wins():
    m = {"/a": "broad", "/a/b": "specific"}
    assert match_override("/a/b/c", m) == "specific"


def test_match_override_no_partial_segment_match():
    # "/a/bc" must NOT match key "/a/b"
    m = {"/a/b": "repo1"}
    assert match_override("/a/bc", m) is None


def test_match_override_none_when_unmapped():
    assert match_override("/x/y", {"/a/b": "repo1"}) is None


def test_match_override_returns_tuple_value():
    m = {"/a/b": ("proj1", "repo1")}
    assert match_override("/a/b", m) == ("proj1", "repo1")


def test_load_overrides_missing_file_is_empty(tmp_path):
    assert load_overrides(tmp_path / "nope.toml") == {}


def test_load_overrides_parses_table(tmp_path):
    f = tmp_path / "m.toml"
    f.write_text('[overrides]\n"/home/me/proj" = "repo-x"\n')
    assert load_overrides(f) == {"/home/me/proj": "repo-x"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest capture/tests/test_overrides.py -v`
Expected: FAIL — module `capture.claude_code.overrides` does not exist.

- [ ] **Step 3: Implement the loader and matcher**

```python
# capture/claude_code/overrides.py
"""Load and match the cwd -> repository_id override map.

For sessions whose cwd has no resolvable git remote (moved/deleted folders,
local-only dirs), this map routes them to a real repository instead of the
catch-all. The same map is consumed by the capture adapter (identity
resolution) and the reclassify maintenance command.
"""

import tomllib
from pathlib import Path

DEFAULT_OVERRIDES_PATH = Path(__file__).with_name("cwd_overrides.toml")


def load_overrides(path: Path | str | None = None) -> dict[str, str]:
    """Return the cwd -> repository_id table; missing file yields an empty map."""
    p = Path(path) if path is not None else DEFAULT_OVERRIDES_PATH
    if not p.exists():
        return {}
    data = tomllib.loads(p.read_text())
    return dict(data.get("overrides", {}))


def match_override(cwd: str, mapping: dict):
    """Longest-prefix match of cwd against mapping keys; return the value or None.

    A key matches when cwd equals it or is a path-segment child of it
    (cwd == key or cwd starts with key + "/"). The longest matching key wins,
    so a more specific mapping overrides a broader one.
    """
    best_key: str | None = None
    for key in mapping:
        if cwd == key or cwd.startswith(key + "/"):
            if best_key is None or len(key) > len(best_key):
                best_key = key
    return mapping[best_key] if best_key is not None else None
```

- [ ] **Step 4: Create the real override map file**

```toml
# capture/claude_code/cwd_overrides.toml
# cwd -> repository_id for sessions whose recorded cwd has no resolvable git
# remote (moved/deleted folders). Consumed by the capture adapter and the
# reclassify command. Paths are host-specific (like .env / repositories.local_path).
[overrides]
"/home/bhuvanesh/leadle_master_claude" = "leadle-os"
"/home/bhuvanesh/AI_Native_Workspace/30-leadle-systems/leadle_gtm_intelligence" = "leadle-os"
"/home/bhuvanesh/AI_Native_Workspace/30-leadle-systems/leadle_content_studio" = "leadle-content-studio"
"/home/bhuvanesh/Claude Folders/AI_Native_Plans" = "ai-native-revops-work-brain"
"/home/bhuvanesh/Claude Folders/claude_sessions_project" = "ai-work-journal"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest capture/tests/test_overrides.py -v`
Expected: PASS (8 tests).

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff format capture
.venv/bin/ruff check capture
git add capture/claude_code/overrides.py capture/claude_code/cwd_overrides.toml capture/tests/test_overrides.py
git commit -m "feat: cwd override map loader and longest-prefix matcher

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Adapter resolves no-remote cwds via the override map

**Files:**
- Modify: `capture/claude_code/identity.py`
- Modify: `capture/claude_code/cli.py`
- Test: `capture/tests/test_identity_resolve.py`, `capture/tests/test_cli_e2e.py`

**Interfaces:**
- Consumes: `overrides.match_override` (Task 2).
- Produces:
  - `identity.resolve_repository(cwd, registry, overrides=None) -> RepoResolution` — when `cwd` has no git remote and `overrides` (a `dict[str, tuple[project_id, repository_id]]`) has a longest-prefix match, returns `RepoResolution("registered", None, project_id, repository_id)`; otherwise unchanged (`local_only`).
  - `cli.process(..., cwd_overrides: dict[str, str] | None = None)` — builds the resolved override map from registered repos and routes matching no-remote files to the mapped repo (counted as `files_processed`).

- [ ] **Step 1: Write the failing identity test**

```python
# capture/tests/test_identity_resolve.py — append

def test_resolve_uses_override_map_when_no_remote(tmp_path):
    # tmp_path is not a git repo -> no remote -> override map should resolve it.
    overrides = {str(tmp_path): ("proj-x", "repo-x")}
    res = resolve_repository(str(tmp_path), {}, overrides)
    assert res == RepoResolution("registered", None, "proj-x", "repo-x")


def test_resolve_local_only_when_no_remote_and_not_in_overrides(tmp_path):
    res = resolve_repository(str(tmp_path), {}, {"/other/path": ("p", "r")})
    assert res.status == "local_only"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest capture/tests/test_identity_resolve.py -v`
Expected: FAIL — `resolve_repository()` takes 2 positional args / `overrides` unexpected.

- [ ] **Step 3: Add the overrides branch to `resolve_repository`**

In `capture/claude_code/identity.py`, add the import near the top (with the other imports):

```python
from capture.claude_code.overrides import match_override
```

Replace the `resolve_repository` function with:

```python
def resolve_repository(
    cwd: str,
    registry: dict[str, tuple[str, str]],
    overrides: dict[str, tuple[str, str]] | None = None,
) -> RepoResolution:
    """Resolve a transcript's cwd to a repository.

    Order: git remote -> registry (registered/pending). If the cwd has no
    remote, fall back to the cwd override map (longest-prefix) before declaring
    it local_only. ``overrides`` maps cwd -> (project_id, repository_id).
    """
    canon = canonicalize_remote(get_git_remote(cwd))
    if canon is None:
        if overrides:
            hit = match_override(cwd, overrides)
            if hit:
                return RepoResolution("registered", None, hit[0], hit[1])
        return RepoResolution("local_only", None)
    hit = registry.get(canon)
    if hit:
        return RepoResolution("registered", canon, hit[0], hit[1])
    return RepoResolution("pending", canon)
```

- [ ] **Step 4: Run identity test to verify pass**

Run: `.venv/bin/pytest capture/tests/test_identity_resolve.py -v`
Expected: PASS (existing resolve tests + 2 new).

- [ ] **Step 5: Write the failing CLI e2e test**

```python
# capture/tests/test_cli_e2e.py — append

def test_no_remote_cwd_routed_via_override_map(asgi_client, tmp_path):
    # Register a real repo, then map a no-remote cwd to it via cwd_overrides.
    from capture.claude_code import client as client_mod
    from capture.claude_code.discovery import discover_transcripts

    repo_id = f"pytest-repo-{uuid4().hex}"
    client_mod_resp = asgi_client.post(
        "/repositories",
        json={
            "repository_id": repo_id,
            "project_id": "agentops-core",
            "repository_name": "Override Target",
        },
    )
    assert client_mod_resp.status_code in (200, 201)

    session_id = f"pytest-session-{uuid4().hex}"
    cwd = str(tmp_path / "mapped-folder")
    import os

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
```

(Note: `_write_local_transcript` already exists in this file and writes the transcript under `tmp_path / "proj-slug"`. The `cwd` argument is the recorded cwd in the lines — that is what the override map matches on. `uuid4`, `get_connection`, and `cli` are already imported at the top of this file.)

- [ ] **Step 6: Run to verify failure**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest capture/tests/test_cli_e2e.py::test_no_remote_cwd_routed_via_override_map -v`
Expected: FAIL — `process()` got an unexpected keyword `cwd_overrides`.

- [ ] **Step 7: Thread `cwd_overrides` through `process`**

In `capture/claude_code/cli.py`, add the import at the top (with the other `capture.claude_code` imports):

```python
from capture.claude_code.overrides import load_overrides
```

Change the `process` signature to add the parameter (after `exclude`):

```python
def process(
    http: httpx.Client,
    api_url: str,
    files: list[Path],
    dry_run: bool = False,
    now: float | None = None,
    catch_all: str | None = None,
    exclude: list[str] | None = None,
    cwd_overrides: dict[str, str] | None = None,
) -> RunReport:
```

Immediately after `registry = identity.build_registry(repos)` (and before the `catch_all_ids` block), build the resolved override map:

```python
    cwd_overrides = cwd_overrides or {}
    id_to_project = {r["repository_id"]: r["project_id"] for r in repos}
    resolved_overrides = {
        cwd: (id_to_project[rid], rid)
        for cwd, rid in cwd_overrides.items()
        if rid in id_to_project
    }
```

Then change the resolution call inside the file loop from:

```python
        resolution = identity.resolve_repository(cwd, registry)
```

to:

```python
        resolution = identity.resolve_repository(cwd, registry, resolved_overrides)
```

(No other change to the loop: an override-resolved file has `status == "registered"`, `routed_catch_all` stays `False`, so it is counted as `files_processed` — exactly as a remote-resolved repo. The rest of `process` is unchanged.)

- [ ] **Step 8: Wire the map into `main`**

In `capture/claude_code/cli.py`, add an argument inside `main` (alongside `--exclude`):

```python
    parser.add_argument(
        "--cwd-map",
        default=os.environ.get("AGENTOPS_CWD_MAP"),
        help="Path to a cwd->repository_id override TOML (default: bundled cwd_overrides.toml)",
    )
```

And pass the loaded map into `process` (extend the existing `process(...)` call in `main`):

```python
        report = process(
            http,
            args.api_url,
            files,
            dry_run=args.dry_run,
            catch_all=args.catch_all,
            exclude=args.exclude,
            cwd_overrides=load_overrides(args.cwd_map),
        )
```

- [ ] **Step 9: Run the full capture suite to verify pass**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest capture -p no:warnings`
Expected: PASS (all capture tests, including the new e2e).

- [ ] **Step 10: Lint and commit**

```bash
.venv/bin/ruff format capture
.venv/bin/ruff check capture
git add capture/claude_code/identity.py capture/claude_code/cli.py capture/tests/test_identity_resolve.py capture/tests/test_cli_e2e.py
git commit -m "feat: resolve no-remote cwds via the override map

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Reclassify maintenance command

**Files:**
- Create: `capture/claude_code/reclassify.py`
- Test: `capture/tests/test_reclassify.py`

**Interfaces:**
- Consumes: `overrides.load_overrides` (Task 2); `app.database.get_connection`; `ingestion.get_repository` (existing).
- Produces:
  - `reclassify(conn, overrides: dict[str, str]) -> dict[str, int]` — for each `cwd → repository_id`, `UPDATE`s catch-all (`unsorted-local`) runs whose `cwd` equals or is a child of the mapped cwd to that repo + its project; returns `{repository_id: rows_moved}`. Repos not registered are skipped.
  - `main(argv=None) -> int` — loads the bundled map (or `--cwd-map`), runs `reclassify`, prints per-repo and total counts.

- [ ] **Step 1: Write the failing test**

```python
# capture/tests/test_reclassify.py
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
```

- [ ] **Step 2: Run to verify failure**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest capture/tests/test_reclassify.py -v`
Expected: FAIL — module `capture.claude_code.reclassify` does not exist.

- [ ] **Step 3: Implement the reclassify command**

```python
# capture/claude_code/reclassify.py
"""Reclassify catch-all runs into their real repositories via the cwd map.

Host-local maintenance command: connects directly to Postgres (not via the
HTTP API) and re-points runs whose recorded cwd matches the override map off
the catch-all repository. Events follow their run (no event rewrite).
"""

import argparse

import psycopg

from app.database import get_connection
from app.models import ingestion
from capture.claude_code.overrides import load_overrides

CATCH_ALL_REPOSITORY_ID = "unsorted-local"


def reclassify(conn: psycopg.Connection, overrides: dict[str, str]) -> dict[str, int]:
    """Move catch-all runs to their mapped repos. Returns {repository_id: rows_moved}.

    For each cwd -> repository_id, runs currently in the catch-all whose cwd
    equals the mapped cwd or is a path-segment child of it are re-pointed to the
    repository and its project. Unregistered target repos are skipped.
    """
    moved: dict[str, int] = {}
    for cwd, repository_id in overrides.items():
        repo = ingestion.get_repository(conn, repository_id)
        if repo is None:
            continue
        result = conn.execute(
            """
            UPDATE runs
            SET repository_id = %s, project_id = %s, updated_at = NOW()
            WHERE repository_id = %s
              AND (cwd = %s OR cwd LIKE %s)
            """,
            (repository_id, repo["project_id"], CATCH_ALL_REPOSITORY_ID, cwd, cwd + "/%"),
        )
        moved[repository_id] = moved.get(repository_id, 0) + result.rowcount
    return moved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reclassify catch-all runs into real repositories via the cwd map"
    )
    parser.add_argument(
        "--cwd-map",
        default=None,
        help="Path to cwd_overrides.toml (default: the bundled map)",
    )
    args = parser.parse_args(argv)

    overrides = load_overrides(args.cwd_map)
    with get_connection() as conn:
        moved = reclassify(conn, overrides)

    for repository_id, count in sorted(moved.items()):
        print(f"{repository_id}: {count}")
    print(f"total moved: {sum(moved.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest capture/tests/test_reclassify.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format capture
.venv/bin/ruff check capture
git add capture/claude_code/reclassify.py capture/tests/test_reclassify.py
git commit -m "feat: reclassify command re-points catch-all runs via cwd map

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Reference data, real reclassification, and docs

**Files:**
- Modify: `database/seed.sql`
- Create: `docs/decisions/0005-repository-registration-and-reclassification.md`
- Modify: `README.md`

**Interfaces:** none (reference data + manual application + documentation).

- [ ] **Step 1: Add the projects and repositories to `seed.sql`**

In `database/seed.sql`, inside the existing `BEGIN; ... COMMIT;` block (before `COMMIT;`), append idempotent upserts. Add the three projects:

```sql
-- ------------------------------------------------------------
-- ADDITIONAL PROJECTS (reclassified from catch-all, 2026-06-21)
-- ------------------------------------------------------------
INSERT INTO projects (project_id, project_name, category, status, owner, description)
VALUES
    ('leadle', 'Leadle', 'product', 'active', 'bhuvanesh',
     'Leadle GTM / revenue-ops client work (dashboards, design system).'),
    ('ai-native-work-brain', 'AI Native Work Brain', 'product', 'active', 'bhuvanesh',
     'Supabase + TypeScript Fathom-to-knowledge RevOps work-brain.'),
    ('ai-work-journal', 'AI Work Journal', 'product', 'active', 'bhuvanesh',
     'Narrative devlog / session-capture product (north-star journal wedge).')
ON CONFLICT (project_id) DO UPDATE SET
    project_name = EXCLUDED.project_name,
    category     = EXCLUDED.category,
    status       = EXCLUDED.status,
    owner        = EXCLUDED.owner,
    description  = EXCLUDED.description,
    updated_at   = NOW();
```

Then add the four repositories:

```sql
-- ------------------------------------------------------------
-- ADDITIONAL REPOSITORIES (canonical remotes; local_path host-specific)
-- ------------------------------------------------------------
INSERT INTO repositories (repository_id, project_id, repository_name, remote_url, local_path, default_branch, is_active)
VALUES
    ('leadle-os', 'leadle', 'Leadle OS',
     'https://github.com/Bhuvanesh31/leadle-os.git',
     '/home/bhuvanesh/AI_Native_Workspace/30-leadle-systems/leadle_gtm_intelligence', 'main', TRUE),
    ('leadle-content-studio', 'leadle', 'Leadle Content Studio',
     'https://github.com/Bhuvanesh31/leadle-content-studio.git',
     '/home/bhuvanesh/AI_Native_Workspace/30-leadle-systems/leadle_content_studio', 'main', TRUE),
    ('ai-native-revops-work-brain', 'ai-native-work-brain', 'AI Native RevOps Work Brain',
     'https://github.com/Bhuvanesh31/ai-native-revops-work-brain.git',
     '/home/bhuvanesh/AI_Native_Workspace/20-products/ai_native_work_brain', 'main', TRUE),
    ('ai-work-journal', 'ai-work-journal', 'AI Work Journal',
     'https://github.com/Bhuvanesh31/ai-work-journal.git',
     '/home/bhuvanesh/AI_Native_Workspace/20-products/ai_work_journal', 'main', TRUE)
ON CONFLICT (repository_id) DO UPDATE SET
    project_id      = EXCLUDED.project_id,
    repository_name = EXCLUDED.repository_name,
    remote_url      = EXCLUDED.remote_url,
    local_path      = EXCLUDED.local_path,
    default_branch  = EXCLUDED.default_branch,
    is_active       = EXCLUDED.is_active,
    updated_at      = NOW();
```

- [ ] **Step 2: Apply the seed and verify the reference data**

Run:
```bash
set -a; . ./.env; set +a
docker exec -i agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < database/seed.sql
docker exec agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT count(*) FROM projects WHERE project_id IN ('leadle','ai-native-work-brain','ai-work-journal');
   SELECT count(*) FROM repositories WHERE repository_id IN ('leadle-os','leadle-content-studio','ai-native-revops-work-brain','ai-work-journal');"
```
Expected: `3` then `4`.

- [ ] **Step 3: Run the real reclassification**

Run:
```bash
set -a; . ./.env; set +a
.venv/bin/python -m capture.claude_code.reclassify
```
Expected output (counts):
```
ai-native-revops-work-brain: 46
ai-work-journal: 11
leadle-content-studio: 1
leadle-os: 19
total moved: 77
```
(77 = the 78 catch-all sessions minus the 1 `30-leadle-systems` root session, which stays `unsorted`.)

- [ ] **Step 4: Verify the catch-all is drained and events followed**

Run:
```bash
set -a; . ./.env; set +a
docker exec agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT project_id||' '||repository_id||' '||count(*) FROM runs GROUP BY 1,2 ORDER BY 3 DESC;
   SELECT 'unsorted_remaining='||count(*) FROM runs WHERE repository_id='unsorted-local';"
```
Expected: `unsorted_remaining=1`; the 4 new repos now carry their session counts (19/1/46/11); `run_events` still join to their runs (no orphaned events — events move with the run via `run_id`).

- [ ] **Step 5: Add the decision record**

```markdown
# docs/decisions/0005-repository-registration-and-reclassification.md
# Decision 0005: Repository registration + catch-all reclassification

## Status

Accepted

## Decision

Add a `POST /repositories` write endpoint for self-service repository
registration, and reclassify catch-all (`unsorted`) sessions into their real
repositories using a shared `cwd -> repository_id` override map consumed by both
the capture adapter and a host-local `reclassify` command.

## Reason

- 78 of 106 captured sessions sat in the catch-all because their projects were
  moved into the workspace tree, leaving the recorded cwd without a resolvable
  git remote. Re-extraction alone cannot reclassify them.
- A single override map keeps reclassification of stored runs and routing of
  future backfills consistent, so a full re-backfill never re-pollutes the
  catch-all.
- Reclassifying is a pure `UPDATE runs` (events hang off `run_id`); no schema
  change and no event rewrite.

## Boundaries

Reclassify is a host-local maintenance command (direct DB), not an API endpoint
— promote later only if a remote desktop or UI needs it. OTEL ingestion, a
generic `local_path` resolution layer, and `POST /projects` are out of scope.
See `docs/superpowers/specs/2026-06-21-capture-polish-design.md`.
```

- [ ] **Step 6: Document in the README**

```markdown
<!-- README.md — append to the "Claude Code capture" section -->
### Repository registration & reclassification

Register a repository (so its sessions attribute to a real project) with
`POST /repositories` (`repository_id`, `project_id`, `repository_name`, and
optional `remote_url` / `local_path`). Sessions resolve to a repository by their
canonical git remote; sessions whose recorded cwd has no remote (moved or
deleted folders) are mapped by `capture/claude_code/cwd_overrides.toml`
(`cwd -> repository_id`), which the capture adapter consults before the
catch-all. To re-point already-stored catch-all runs, run the host-local
command: `python -m capture.claude_code.reclassify`.
```

- [ ] **Step 7: Commit the docs and seed**

```bash
git add database/seed.sql docs/decisions/0005-repository-registration-and-reclassification.md README.md
git commit -m "docs: seed reclassification reference data + ADR 0005 + README

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- §5.1 `POST /repositories` (schema, upsert model, route, validation) → Task 1. ✓
- §5.2 override map (TOML file, `load_overrides`, `match_override` longest-prefix) → Task 2. ✓
- §5.3 adapter resolution via map (`resolve_repository` overrides param; `process` `cwd_overrides`; counted as processed) → Task 3. ✓
- §5.4 reclassify command (host-local direct `UPDATE`, idempotent, skip unregistered) → Task 4. ✓
- §5.5 reference data (3 projects + 4 repos seeded) → Task 5. ✓
- §3 finalized mapping (all 78; root stays unsorted) → Task 4 logic + Task 5 real run (77 moved, 1 remains). ✓
- §6 data flow / §7 error handling (unknown project 400; missing file → empty; unregistered repo skipped) → Tasks 1, 2, 4 tests. ✓
- §8 testing (endpoint, overrides matcher, adapter routing, reclassify) → Tasks 1–4 tests. ✓
- §9 scope (no OTEL, no generic local_path, no POST /projects) → not implemented anywhere. ✓

**2. Placeholder scan:** No TBD/TODO; every code step has complete code; every test step has assertions; expected command outputs are concrete. ✓

**3. Type consistency:** `resolve_repository(cwd, registry, overrides=None)` — `overrides` is `dict[cwd -> (project_id, repository_id)]` in Task 3, built in `process` from the raw `dict[cwd -> repository_id]` (Task 2/3). `match_override` returns the mapping's value type (tuple in the adapter path). `upsert_repository` keyword signature matches `RepositoryCreate.model_dump()` (Task 1) and the explicit-kwargs calls in Task 4 tests. `reclassify(conn, overrides: dict[str,str]) -> dict[str,int]` consistent across Task 4 def, tests, and `main`. `CATCH_ALL_REPOSITORY_ID = "unsorted-local"` matches the seed. ✓
