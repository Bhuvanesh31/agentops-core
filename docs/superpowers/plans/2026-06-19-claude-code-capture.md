# Claude Code Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone CLI tool that reads local Claude Code session transcripts, normalizes them into the shared run-event model, and submits them to the existing `POST /runs/events` ingestion endpoint — idempotently and across desktops.

**Architecture:** A read-and-replay CLI (`capture/claude_code/`) with focused, independently testable units: discovery → identity resolution (git remote → registered repo) → normalization (transcript line → meaningful events) → API client → report. Reuses `app/redaction.py`. Adds one read-only `GET /repositories` endpoint to the API for identity resolution. Idempotency comes from the DB's `UNIQUE(source_event_id)` constraint, keyed on per-line `uuid`s and `tool_use` block ids.

**Tech Stack:** Python 3.12, FastAPI + psycopg 3 (existing API), httpx (CLI HTTP client), pytest, ruff. No ORM.

## Global Constraints

- Python `>=3.12`; psycopg 3 with raw parameterized SQL, no ORM.
- Reuse `app/redaction.py` for secret redaction; do not reimplement.
- Tool slug is always `"claude-code"`.
- Normalized **meaningful-event** grain only; drop UI/bookkeeping line types.
- `source_event_id`: `assistant_message`/`user_prompt`/`pr_link` → line `uuid`; `file_edit`/`command_run`/`tool_use` → `tool_use` block `id`; synthesized → `"<sessionId>#session_started"` / `"#session_ended"`.
- Repository identity is keyed on the **canonical git remote URL**, never `cwd`.
- v1 excludes: hooks, OTEL ingestion, `usage_metrics` aggregation, `commits` population, repo-registration (write) endpoint, Codex, Work Journal UI, scoring, Qdrant.
- ruff: `line-length = 100`, lint select `E, F, I, UP, B` (FastAPI `Depends` is an immutable call).
- Tests run against the live Postgres container; create rows with ids prefixed `pytest-` and rely on the cleanup fixture. Sanitized synthetic transcripts only — never real session files.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Branch: `feat/claude-code-capture`.

---

### Task 1: `GET /repositories` endpoint (API)

**Files:**
- Modify: `app/models/ingestion.py` (add `list_repositories`)
- Create: `app/schemas/repositories.py`
- Create: `app/routes/repositories.py`
- Modify: `app/main.py` (register router)
- Test: `tests/test_repositories.py`

**Interfaces:**
- Produces: `ingestion.list_repositories(conn) -> list[dict]` with keys `repository_id`, `project_id`, `remote_url`. HTTP `GET /repositories` → `200` JSON array of `{repository_id, project_id, remote_url}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repositories.py
def test_list_repositories_includes_seed(client):
    response = client.get("/repositories")
    assert response.status_code == 200
    repos = response.json()
    ids = {r["repository_id"] for r in repos}
    assert "agentops-core-main" in ids
    seeded = next(r for r in repos if r["repository_id"] == "agentops-core-main")
    assert seeded["project_id"] == "agentops-core"
    assert seeded["remote_url"] and "github.com" in seeded["remote_url"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_repositories.py -v`
Expected: FAIL — `404 Not Found` (route does not exist yet).

- [ ] **Step 3: Add the data-access function**

```python
# app/models/ingestion.py  (add at end of file)
def list_repositories(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Return active repositories for identity resolution."""
    return conn.execute(
        """
        SELECT repository_id, project_id, remote_url
        FROM repositories
        WHERE is_active = TRUE
        ORDER BY repository_id
        """
    ).fetchall()
```

- [ ] **Step 4: Add the response schema**

```python
# app/schemas/repositories.py
"""Repository response schema for identity resolution."""

from pydantic import BaseModel


class RepositoryOut(BaseModel):
    repository_id: str
    project_id: str
    remote_url: str | None = None
```

- [ ] **Step 5: Add the route**

```python
# app/routes/repositories.py
"""Read-only repository listing, used by capture adapters to resolve
a session's git remote to a registered (project_id, repository_id)."""

import psycopg
from fastapi import APIRouter, Depends

from app.database import db_dependency
from app.models import ingestion
from app.schemas.repositories import RepositoryOut

router = APIRouter(tags=["repositories"])


@router.get("/repositories", response_model=list[RepositoryOut])
def list_repositories(conn: psycopg.Connection = Depends(db_dependency)) -> list[dict]:
    return ingestion.list_repositories(conn)
```

- [ ] **Step 6: Register the router**

```python
# app/main.py — update the import and registration
from app.routes import events, health, repositories
```
```python
# app/main.py — inside create_app(), after the existing include_router calls
    app.include_router(repositories.router)
```

- [ ] **Step 7: Run test to verify it passes**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_repositories.py -v`
Expected: PASS.

- [ ] **Step 8: Lint and commit**

```bash
.venv/bin/ruff format app tests
.venv/bin/ruff check app tests
git add app/models/ingestion.py app/schemas/repositories.py app/routes/repositories.py app/main.py tests/test_repositories.py
git commit -m "feat: add read-only GET /repositories endpoint

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Package scaffolding + remote-URL canonicalization

**Files:**
- Create: `capture/__init__.py`, `capture/claude_code/__init__.py`, `capture/tests/__init__.py`
- Create: `capture/claude_code/identity.py` (canonicalization only here)
- Modify: `pyproject.toml` (test paths + wheel package)
- Test: `capture/tests/test_identity_canonical.py`

**Interfaces:**
- Produces: `identity.canonicalize_remote(url: str | None) -> str | None` → `github.com/<owner>/<repo>` form, or `None` for empty input.

- [ ] **Step 1: Create empty package markers**

```python
# capture/__init__.py
"""AgentOps capture adapters."""
```
```python
# capture/claude_code/__init__.py
"""Claude Code transcript-replay capture adapter."""
```
```python
# capture/tests/__init__.py
```

- [ ] **Step 2: Wire tests + packaging in pyproject.toml**

```toml
# pyproject.toml — replace the [tool.pytest.ini_options] testpaths line
[tool.pytest.ini_options]
testpaths = ["tests", "capture/tests"]
addopts = "-q"
```
```toml
# pyproject.toml — replace the [tool.hatch.build.targets.wheel] packages line
[tool.hatch.build.targets.wheel]
packages = ["app", "capture"]
```

- [ ] **Step 3: Write the failing test**

```python
# capture/tests/test_identity_canonical.py
import pytest

from capture.claude_code.identity import canonicalize_remote


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://github.com/Bhuvanesh31/agentops-core.git", "github.com/Bhuvanesh31/agentops-core"),
        ("git@github.com:Bhuvanesh31/agentops-core.git", "github.com/Bhuvanesh31/agentops-core"),
        ("ssh://git@github.com/Bhuvanesh31/agentops-core.git", "github.com/Bhuvanesh31/agentops-core"),
        ("https://github.com/Bhuvanesh31/agentops-core", "github.com/Bhuvanesh31/agentops-core"),
        ("https://GitHub.com/Bhuvanesh31/agentops-core.git", "github.com/Bhuvanesh31/agentops-core"),
        (None, None),
        ("", None),
        ("   ", None),
    ],
)
def test_canonicalize_remote(raw, expected):
    assert canonicalize_remote(raw) == expected
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/bin/pytest capture/tests/test_identity_canonical.py -v`
Expected: FAIL — `ModuleNotFoundError: capture.claude_code.identity`.

- [ ] **Step 5: Implement canonicalization**

```python
# capture/claude_code/identity.py
"""Repository identity resolution for transcripts.

Sessions are keyed to repositories by their canonical git remote URL, which is
stable across machines (unlike cwd). This module canonicalizes remotes and
resolves a working directory to a registered (project_id, repository_id).
"""

import re


def canonicalize_remote(url: str | None) -> str | None:
    """Normalize a git remote URL to ``host/owner/repo`` (host lowercased).

    Handles https, ssh://, and scp-like ``git@host:owner/repo`` forms, strips a
    trailing ``.git`` and slashes. Returns None for empty input.
    """
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    u = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", u)  # strip scheme://
    u = re.sub(r"^[^@/]+@", "", u)  # strip user@
    if ":" in u and "/" in u:
        head, _, tail = u.partition(":")
        if "/" not in head:  # scp-like host:owner/repo
            u = f"{head}/{tail}"
    elif ":" in u:
        u = u.replace(":", "/", 1)
    u = u.rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    host, slash, rest = u.partition("/")
    return f"{host.lower()}{slash}{rest}"
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest capture/tests/test_identity_canonical.py -v`
Expected: PASS (all 8 parameter cases).

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff format capture
.venv/bin/ruff check capture
git add capture pyproject.toml
git commit -m "feat: scaffold capture package + git remote canonicalization

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Git remote read + repository resolution

**Files:**
- Modify: `capture/claude_code/identity.py`
- Test: `capture/tests/test_identity_resolve.py`

**Interfaces:**
- Consumes: `canonicalize_remote` (Task 2).
- Produces:
  - `identity.get_git_remote(cwd: str) -> str | None`
  - `identity.build_registry(repositories: list[dict]) -> dict[str, tuple[str, str]]` mapping canonical-remote → `(project_id, repository_id)`.
  - `identity.RepoResolution` dataclass: `status` (`"registered"|"pending"|"local_only"`), `canonical_remote: str | None`, `project_id: str | None`, `repository_id: str | None`.
  - `identity.resolve_repository(cwd: str, registry: dict) -> RepoResolution`.

- [ ] **Step 1: Write the failing test**

```python
# capture/tests/test_identity_resolve.py
import subprocess

from capture.claude_code.identity import (
    RepoResolution,
    build_registry,
    get_git_remote,
    resolve_repository,
)


def _init_repo(path, remote_url):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=path, check=True)


def test_get_git_remote_reads_origin(tmp_path):
    _init_repo(tmp_path, "https://github.com/acme/widget.git")
    assert get_git_remote(str(tmp_path)) == "https://github.com/acme/widget.git"


def test_get_git_remote_none_when_not_a_repo(tmp_path):
    assert get_git_remote(str(tmp_path)) is None


def test_build_registry_canonicalizes_keys():
    registry = build_registry(
        [{"repository_id": "r1", "project_id": "p1", "remote_url": "git@github.com:acme/widget.git"}]
    )
    assert registry == {"github.com/acme/widget": ("p1", "r1")}


def test_resolve_registered(tmp_path):
    _init_repo(tmp_path, "https://github.com/acme/widget.git")
    registry = {"github.com/acme/widget": ("p1", "r1")}
    res = resolve_repository(str(tmp_path), registry)
    assert res == RepoResolution("registered", "github.com/acme/widget", "p1", "r1")


def test_resolve_pending_when_remote_unknown(tmp_path):
    _init_repo(tmp_path, "https://github.com/acme/unknown.git")
    res = resolve_repository(str(tmp_path), {})
    assert res.status == "pending"
    assert res.canonical_remote == "github.com/acme/unknown"
    assert res.repository_id is None


def test_resolve_local_only_when_no_remote(tmp_path):
    res = resolve_repository(str(tmp_path), {})
    assert res.status == "local_only"
    assert res.canonical_remote is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest capture/tests/test_identity_resolve.py -v`
Expected: FAIL — `ImportError: cannot import name 'RepoResolution'`.

- [ ] **Step 3: Implement remote read + resolution**

```python
# capture/claude_code/identity.py — add imports at top
import subprocess
from dataclasses import dataclass
```
```python
# capture/claude_code/identity.py — append below canonicalize_remote

@dataclass
class RepoResolution:
    status: str  # "registered" | "pending" | "local_only"
    canonical_remote: str | None
    project_id: str | None = None
    repository_id: str | None = None


def get_git_remote(cwd: str) -> str | None:
    """Return the origin remote URL for ``cwd``, or None if unavailable."""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def build_registry(repositories: list[dict]) -> dict[str, tuple[str, str]]:
    """Map canonical remote -> (project_id, repository_id) from GET /repositories."""
    registry: dict[str, tuple[str, str]] = {}
    for repo in repositories:
        canon = canonicalize_remote(repo.get("remote_url"))
        if canon:
            registry[canon] = (repo["project_id"], repo["repository_id"])
    return registry


def resolve_repository(cwd: str, registry: dict[str, tuple[str, str]]) -> RepoResolution:
    """Resolve a transcript's cwd to a registered repository via its git remote."""
    canon = canonicalize_remote(get_git_remote(cwd))
    if canon is None:
        return RepoResolution("local_only", None)
    hit = registry.get(canon)
    if hit:
        return RepoResolution("registered", canon, hit[0], hit[1])
    return RepoResolution("pending", canon)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest capture/tests/test_identity_resolve.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format capture
.venv/bin/ruff check capture
git add capture/claude_code/identity.py capture/tests/test_identity_resolve.py
git commit -m "feat: resolve cwd to registered repository via git remote

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Transcript discovery

**Files:**
- Create: `capture/claude_code/discovery.py`
- Test: `capture/tests/test_discovery.py`

**Interfaces:**
- Produces: `discovery.discover_transcripts(projects_dir: str, only: str | None = None, since: float | None = None) -> list[pathlib.Path]` — sorted `*/*.jsonl` files under `projects_dir`, filtered by substring `only` (matched against the parent dir name) and `since` (epoch seconds, file mtime ≥ since).

- [ ] **Step 1: Write the failing test**

```python
# capture/tests/test_discovery.py
import os

from capture.claude_code.discovery import discover_transcripts


def _make(base, slug, name, mtime=None):
    d = base / slug
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text("{}\n")
    if mtime is not None:
        os.utime(f, (mtime, mtime))
    return f


def test_discovers_jsonl_sorted(tmp_path):
    _make(tmp_path, "proj-a", "s1.jsonl")
    _make(tmp_path, "proj-b", "s2.jsonl")
    (tmp_path / "proj-a" / "notes.txt").write_text("ignore")
    found = discover_transcripts(str(tmp_path))
    names = [p.name for p in found]
    assert names == ["s1.jsonl", "s2.jsonl"]


def test_only_filters_by_parent_slug(tmp_path):
    _make(tmp_path, "proj-a", "s1.jsonl")
    _make(tmp_path, "proj-b", "s2.jsonl")
    found = discover_transcripts(str(tmp_path), only="proj-b")
    assert [p.name for p in found] == ["s2.jsonl"]


def test_since_filters_by_mtime(tmp_path):
    _make(tmp_path, "proj-a", "old.jsonl", mtime=1000)
    _make(tmp_path, "proj-a", "new.jsonl", mtime=9_000_000_000)
    found = discover_transcripts(str(tmp_path), since=5000)
    assert [p.name for p in found] == ["new.jsonl"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest capture/tests/test_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: capture.claude_code.discovery`.

- [ ] **Step 3: Implement discovery**

```python
# capture/claude_code/discovery.py
"""Find Claude Code transcript files under the projects directory."""

from pathlib import Path


def discover_transcripts(
    projects_dir: str,
    only: str | None = None,
    since: float | None = None,
) -> list[Path]:
    """Return sorted ``*/*.jsonl`` transcripts, filtered by slug and mtime."""
    base = Path(projects_dir).expanduser()
    files: list[Path] = []
    for path in sorted(base.glob("*/*.jsonl")):
        if only and only not in path.parent.name:
            continue
        if since is not None and path.stat().st_mtime < since:
            continue
        files.append(path)
    return files
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest capture/tests/test_discovery.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format capture
.venv/bin/ruff check capture
git add capture/claude_code/discovery.py capture/tests/test_discovery.py
git commit -m "feat: discover Claude Code transcript files

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Line normalization

**Files:**
- Create: `capture/claude_code/normalize.py`
- Test: `capture/tests/test_normalize.py`

**Interfaces:**
- Consumes: `app.redaction.redact_payload`.
- Produces: `normalize.normalize_line(line: dict) -> list[dict]`. Each returned event dict has keys: `event_type`, `session_id`, `cwd`, `branch`, `occurred_at`, `source_event_id`, `files_touched`, `raw_payload`, `model`. (Run-identity fields `tool`/`project_id`/`repository_id` are injected later by the CLI.) Also exports `NOISE_TYPES: set[str]` and `EDIT_TOOLS: set[str]`.

- [ ] **Step 1: Write the failing test**

```python
# capture/tests/test_normalize.py
from capture.claude_code.normalize import normalize_line

BASE = {"sessionId": "s1", "cwd": "/repo", "gitBranch": "main", "timestamp": "2026-06-19T00:00:00Z"}


def test_user_string_prompt_becomes_user_prompt():
    line = {**BASE, "type": "user", "uuid": "u1", "message": {"content": "do the thing"}}
    events = normalize_line(line)
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == "user_prompt"
    assert e["source_event_id"] == "u1"
    assert e["session_id"] == "s1"
    assert e["occurred_at"] == "2026-06-19T00:00:00Z"


def test_user_tool_result_turn_is_dropped():
    line = {**BASE, "type": "user", "uuid": "u2", "message": {"content": [{"type": "tool_result"}]}}
    assert normalize_line(line) == []


def test_user_meta_is_dropped():
    line = {**BASE, "type": "user", "uuid": "u3", "isMeta": True, "message": {"content": "x"}}
    assert normalize_line(line) == []


def test_assistant_text_only_yields_one_event_with_model():
    line = {
        **BASE,
        "type": "assistant",
        "uuid": "a1",
        "message": {"model": "claude-opus-4-8", "content": [{"type": "text", "text": "hi"}]},
    }
    events = normalize_line(line)
    assert len(events) == 1
    assert events[0]["event_type"] == "assistant_message"
    assert events[0]["source_event_id"] == "a1"
    assert events[0]["model"] == "claude-opus-4-8"


def test_assistant_edit_tool_yields_file_edit_with_files_touched():
    line = {
        **BASE,
        "type": "assistant",
        "uuid": "a2",
        "message": {
            "model": "claude-opus-4-8",
            "content": [
                {"type": "text", "text": "editing"},
                {"type": "tool_use", "id": "toolu_1", "name": "Edit", "input": {"file_path": "/repo/x.py"}},
            ],
        },
    }
    events = normalize_line(line)
    types = [e["event_type"] for e in events]
    assert types == ["assistant_message", "file_edit"]
    edit = events[1]
    assert edit["source_event_id"] == "toolu_1"
    assert edit["files_touched"] == ["/repo/x.py"]


def test_assistant_bash_tool_yields_command_run():
    line = {
        **BASE,
        "type": "assistant",
        "uuid": "a3",
        "message": {
            "model": "claude-opus-4-8",
            "content": [{"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {"command": "ls"}}],
        },
    }
    events = normalize_line(line)
    assert events[1]["event_type"] == "command_run"
    assert events[1]["source_event_id"] == "toolu_2"


def test_pr_link_becomes_pr_link_event():
    line = {**BASE, "type": "pr-link", "uuid": "p1", "prNumber": 2, "prUrl": "https://x/pr/2"}
    events = normalize_line(line)
    assert events[0]["event_type"] == "pr_link"
    assert events[0]["raw_payload"]["prUrl"] == "https://x/pr/2"


def test_noise_types_are_dropped():
    for noise in ["attachment", "mode", "permission-mode", "ai-title", "system", "file-history-snapshot"]:
        assert normalize_line({**BASE, "type": noise, "uuid": "n"}) == []


def test_secret_in_user_prompt_is_redacted():
    line = {**BASE, "type": "user", "uuid": "u9", "message": {"content": "key sk-ant-api03-aaaaaaaaaaaaaaaaaaaa"}}
    events = normalize_line(line)
    import json

    assert "sk-ant-api03" not in json.dumps(events[0]["raw_payload"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest capture/tests/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: capture.claude_code.normalize`.

- [ ] **Step 3: Implement normalization**

```python
# capture/claude_code/normalize.py
"""Map Claude Code transcript lines to normalized meaningful events.

Run-identity fields (tool, project_id, repository_id) are injected by the CLI;
this module derives everything obtainable from the line itself and redacts the
payload before it leaves the process.
"""

from typing import Any

from app.redaction import redact_payload

NOISE_TYPES: set[str] = {
    "attachment",
    "mode",
    "permission-mode",
    "last-prompt",
    "ai-title",
    "bridge-session",
    "file-history-snapshot",
    "system",
}

EDIT_TOOLS: set[str] = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _event(
    event_type: str,
    line: dict,
    source_event_id: str,
    raw_payload: dict,
    *,
    files_touched: list[str] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "session_id": line.get("sessionId"),
        "cwd": line.get("cwd"),
        "branch": line.get("gitBranch"),
        "occurred_at": line.get("timestamp"),
        "source_event_id": source_event_id,
        "files_touched": files_touched or [],
        "raw_payload": raw_payload,
        "model": model,
    }


def normalize_line(line: dict) -> list[dict[str, Any]]:
    """Return zero or more normalized events for a single transcript line."""
    line_type = line.get("type")
    if line_type in NOISE_TYPES:
        return []

    if line_type == "user":
        message = line.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or line.get("isMeta") or line.get("toolUseResult") is not None:
            return []  # synthetic tool-result turn or meta line
        payload, _ = redact_payload(line)
        return [_event("user_prompt", line, line.get("uuid"), payload)]

    if line_type == "pr-link":
        payload, _ = redact_payload(line)
        return [_event("pr_link", line, line.get("uuid"), payload)]

    if line_type == "assistant":
        message = line.get("message") or {}
        model = message.get("model")
        events: list[dict[str, Any]] = []
        payload, _ = redact_payload(line)
        events.append(_event("assistant_message", line, line.get("uuid"), payload, model=model))
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                if name in EDIT_TOOLS:
                    event_type = "file_edit"
                elif name == "Bash":
                    event_type = "command_run"
                else:
                    event_type = "tool_use"
                files: list[str] = []
                file_path = (block.get("input") or {}).get("file_path")
                if event_type == "file_edit" and file_path:
                    files = [file_path]
                block_payload, _ = redact_payload(block)
                events.append(
                    _event(
                        event_type,
                        line,
                        block.get("id"),
                        block_payload,
                        files_touched=files,
                        model=model,
                    )
                )
        return events

    return []  # unknown type -> drop
```

- [ ] **Step 4: Run test to verify it passes**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest capture/tests/test_normalize.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format capture
.venv/bin/ruff check capture
git add capture/claude_code/normalize.py capture/tests/test_normalize.py
git commit -m "feat: normalize transcript lines to meaningful events

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Session-boundary synthesis

**Files:**
- Modify: `capture/claude_code/normalize.py`
- Test: `capture/tests/test_session_synthesis.py`

**Interfaces:**
- Produces: `normalize.synthesize_session_events(first_line: dict, last_line: dict, file_mtime: float, now: float, idle_seconds: float = 3600) -> list[dict]`. Always emits `session_started`; emits `session_ended` only when `now - file_mtime >= idle_seconds`. `source_event_id`s are `"<sessionId>#session_started"` / `"#session_ended"`.

- [ ] **Step 1: Write the failing test**

```python
# capture/tests/test_session_synthesis.py
from capture.claude_code.normalize import synthesize_session_events

FIRST = {"sessionId": "s1", "cwd": "/repo", "gitBranch": "main", "timestamp": "2026-06-19T00:00:00Z"}
LAST = {"sessionId": "s1", "cwd": "/repo", "gitBranch": "main", "timestamp": "2026-06-19T01:00:00Z"}


def test_idle_session_gets_started_and_ended():
    events = synthesize_session_events(FIRST, LAST, file_mtime=0.0, now=10_000.0, idle_seconds=3600)
    types = [e["event_type"] for e in events]
    assert types == ["session_started", "session_ended"]
    assert events[0]["source_event_id"] == "s1#session_started"
    assert events[1]["source_event_id"] == "s1#session_ended"
    assert events[0]["occurred_at"] == "2026-06-19T00:00:00Z"
    assert events[1]["occurred_at"] == "2026-06-19T01:00:00Z"


def test_fresh_session_gets_only_started():
    events = synthesize_session_events(FIRST, LAST, file_mtime=9_999.0, now=10_000.0, idle_seconds=3600)
    assert [e["event_type"] for e in events] == ["session_started"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest capture/tests/test_session_synthesis.py -v`
Expected: FAIL — `ImportError: cannot import name 'synthesize_session_events'`.

- [ ] **Step 3: Implement synthesis**

```python
# capture/claude_code/normalize.py — append at end of file

def synthesize_session_events(
    first_line: dict,
    last_line: dict,
    file_mtime: float,
    now: float,
    idle_seconds: float = 3600,
) -> list[dict[str, Any]]:
    """Synthesize session_started (always) and session_ended (only when idle)."""
    session_id = first_line.get("sessionId")
    events = [
        _event(
            "session_started",
            first_line,
            f"{session_id}#session_started",
            {},
        )
    ]
    if now - file_mtime >= idle_seconds:
        events.append(
            _event(
                "session_ended",
                last_line,
                f"{session_id}#session_ended",
                {},
            )
        )
    return events
```

- [ ] **Step 4: Run test to verify it passes**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest capture/tests/test_session_synthesis.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format capture
.venv/bin/ruff check capture
git add capture/claude_code/normalize.py capture/tests/test_session_synthesis.py
git commit -m "feat: synthesize session_started/session_ended events

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: API client (httpx, retry/backoff)

**Files:**
- Create: `capture/claude_code/client.py`
- Modify: `pyproject.toml` (add `httpx` to runtime dependencies)
- Test: `capture/tests/test_client.py`

**Interfaces:**
- Produces:
  - `client.fetch_repositories(http: httpx.Client, api_url: str) -> list[dict]` (GET `/repositories`).
  - `client.post_event(http: httpx.Client, api_url: str, event: dict, retries: int = 3, backoff: float = 0.5, sleep=time.sleep) -> tuple[str, int]` returning `(status, http_status)` where `status` ∈ `{"created","duplicate","error"}`. Retries 5xx/transport errors; 4xx is non-retryable → `("error", code)`.

- [ ] **Step 1: Add httpx as a runtime dependency**

```toml
# pyproject.toml — add to [project] dependencies list
    "httpx>=0.27",
```
Then reinstall so the metadata is current:
```bash
.venv/bin/pip install -e ".[dev]" >/dev/null
```

- [ ] **Step 2: Write the failing test**

```python
# capture/tests/test_client.py
import httpx
import pytest

from capture.claude_code.client import fetch_repositories, post_event


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")


def test_fetch_repositories_returns_list():
    def handler(request):
        assert request.url.path == "/repositories"
        return httpx.Response(200, json=[{"repository_id": "r1", "project_id": "p1", "remote_url": "u"}])

    with _client(handler) as http:
        repos = fetch_repositories(http, "http://test")
    assert repos[0]["repository_id"] == "r1"


def test_post_event_created():
    def handler(request):
        return httpx.Response(201, json={"status": "created", "run_id": "x", "event_id": "y"})

    with _client(handler) as http:
        status, code = post_event(http, "http://test", {"a": 1})
    assert (status, code) == ("created", 201)


def test_post_event_duplicate():
    def handler(request):
        return httpx.Response(200, json={"status": "duplicate", "run_id": "x", "event_id": "y"})

    with _client(handler) as http:
        status, code = post_event(http, "http://test", {"a": 1})
    assert (status, code) == ("duplicate", 200)


def test_post_event_4xx_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, json={"detail": "nope"})

    with _client(handler) as http:
        status, code = post_event(http, "http://test", {"a": 1}, sleep=lambda _: None)
    assert (status, code) == ("error", 404)
    assert calls["n"] == 1


def test_post_event_retries_5xx_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503)
        return httpx.Response(201, json={"status": "created"})

    with _client(handler) as http:
        status, code = post_event(http, "http://test", {"a": 1}, sleep=lambda _: None)
    assert status == "created"
    assert calls["n"] == 2


def test_post_event_raises_after_exhaustion():
    def handler(request):
        return httpx.Response(500)

    with _client(handler) as http:
        with pytest.raises(RuntimeError):
            post_event(http, "http://test", {"a": 1}, retries=2, sleep=lambda _: None)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest capture/tests/test_client.py -v`
Expected: FAIL — `ModuleNotFoundError: capture.claude_code.client`.

- [ ] **Step 4: Implement the client**

```python
# capture/claude_code/client.py
"""HTTP client for submitting normalized events to the ingestion API."""

import time
from collections.abc import Callable

import httpx


def fetch_repositories(http: httpx.Client, api_url: str) -> list[dict]:
    """GET /repositories and return the JSON list."""
    response = http.get(f"{api_url.rstrip('/')}/repositories", timeout=30)
    response.raise_for_status()
    return response.json()


def post_event(
    http: httpx.Client,
    api_url: str,
    event: dict,
    retries: int = 3,
    backoff: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, int]:
    """POST one event. Retries transport errors and 5xx; 4xx is terminal.

    Returns (status, http_status) with status in {"created","duplicate","error"}.
    Raises RuntimeError if retries are exhausted on a retryable failure.
    """
    url = f"{api_url.rstrip('/')}/runs/events"
    last_detail = ""
    for attempt in range(retries):
        try:
            response = http.post(url, json=event, timeout=30)
        except httpx.HTTPError as exc:
            last_detail = str(exc)
            sleep(backoff * (2**attempt))
            continue
        if response.status_code in (200, 201):
            return response.json().get("status", "unknown"), response.status_code
        if response.status_code >= 500:
            last_detail = f"server {response.status_code}"
            sleep(backoff * (2**attempt))
            continue
        return "error", response.status_code  # 4xx: non-retryable
    raise RuntimeError(f"post_event failed after {retries} attempts: {last_detail}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest capture/tests/test_client.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff format capture
.venv/bin/ruff check capture
git add capture/claude_code/client.py capture/tests/test_client.py pyproject.toml
git commit -m "feat: add ingestion API client with retry/backoff

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Run report

**Files:**
- Create: `capture/claude_code/report.py`
- Test: `capture/tests/test_report.py`

**Interfaces:**
- Produces: `report.RunReport` dataclass with int counters `files_processed`, `files_skipped`, `events_created`, `events_duplicate`, `events_error`; dicts `pending_repos` and `local_only` (canonical-remote-or-slug → count); methods `add_pending(canonical_remote: str)`, `add_local_only(label: str)`, and `render() -> str`.

- [ ] **Step 1: Write the failing test**

```python
# capture/tests/test_report.py
from capture.claude_code.report import RunReport


def test_counts_and_render():
    r = RunReport()
    r.files_processed = 2
    r.events_created = 5
    r.events_duplicate = 3
    r.add_pending("github.com/acme/unknown")
    r.add_pending("github.com/acme/unknown")
    r.add_local_only("/home/me/scratch")
    text = r.render()
    assert "created=5" in text
    assert "duplicate=3" in text
    assert "github.com/acme/unknown" in text
    assert "2" in text  # pending count for the repo seen twice
    assert "/home/me/scratch" in text


def test_pending_counts_accumulate():
    r = RunReport()
    r.add_pending("a")
    r.add_pending("a")
    r.add_pending("b")
    assert r.pending_repos == {"a": 2, "b": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest capture/tests/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: capture.claude_code.report`.

- [ ] **Step 3: Implement the report**

```python
# capture/claude_code/report.py
"""Summary + pending/local-only repository reporting for a capture run."""

from dataclasses import dataclass, field


@dataclass
class RunReport:
    files_processed: int = 0
    files_skipped: int = 0
    events_created: int = 0
    events_duplicate: int = 0
    events_error: int = 0
    pending_repos: dict[str, int] = field(default_factory=dict)
    local_only: dict[str, int] = field(default_factory=dict)

    def add_pending(self, canonical_remote: str) -> None:
        self.pending_repos[canonical_remote] = self.pending_repos.get(canonical_remote, 0) + 1

    def add_local_only(self, label: str) -> None:
        self.local_only[label] = self.local_only.get(label, 0) + 1

    def render(self) -> str:
        lines = [
            "AgentOps Claude Code capture",
            "----------------------------",
            f"files: processed={self.files_processed} skipped={self.files_skipped}",
            (
                f"events: created={self.events_created} "
                f"duplicate={self.events_duplicate} error={self.events_error}"
            ),
        ]
        if self.pending_repos:
            lines.append("")
            lines.append("Pending repositories (register, then re-run):")
            for remote, count in sorted(self.pending_repos.items()):
                lines.append(f"  - {remote}  ({count} sessions)")
        if self.local_only:
            lines.append("")
            lines.append("Local-only folders skipped (no git remote):")
            for label, count in sorted(self.local_only.items()):
                lines.append(f"  - {label}  ({count} sessions)")
        return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest capture/tests/test_report.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format capture
.venv/bin/ruff check capture
git add capture/claude_code/report.py capture/tests/test_report.py
git commit -m "feat: add capture run report

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: CLI orchestration + end-to-end

**Files:**
- Create: `capture/claude_code/cli.py`
- Create: `capture/claude_code/__main__.py`
- Create: `capture/tests/conftest.py`
- Test: `capture/tests/test_cli_e2e.py`

**Interfaces:**
- Consumes: `discovery.discover_transcripts`, `identity.{build_registry,resolve_repository}`, `normalize.{normalize_line,synthesize_session_events}`, `client.{fetch_repositories,post_event}`, `report.RunReport`.
- Produces:
  - `cli.load_lines(path) -> list[dict]` (parse a JSONL file, skipping unparseable lines).
  - `cli.events_for_file(path, resolution, now) -> list[dict]` (normalized + synthesized + session-model injected + identity injected; ready to POST).
  - `cli.process(http, api_url, files, dry_run=False, now=None) -> RunReport`.
  - `cli.main(argv=None) -> int`.

- [ ] **Step 1: Create the capture test conftest**

```python
# capture/tests/conftest.py
"""Fixtures for capture tests: an in-process API client and DB cleanup."""

from collections.abc import Iterator

import httpx
import pytest

from app.database import get_connection
from app.main import app


@pytest.fixture
def asgi_client() -> Iterator[httpx.Client]:
    transport = httpx.ASGITransport(app=app)
    with httpx.Client(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture(autouse=True)
def cleanup_pytest_rows() -> Iterator[None]:
    yield
    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM runs WHERE session_id LIKE 'pytest-%'")
            conn.execute("DELETE FROM run_events WHERE source_event_id LIKE 'pytest-%'")
    except Exception:
        pass
```

- [ ] **Step 2: Write the failing end-to-end test**

```python
# capture/tests/test_cli_e2e.py
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
        {**base, "type": "user", "uuid": f"pytest-{session_id}-u1",
         "timestamp": "2026-06-19T00:00:00Z", "message": {"content": "do the thing"}},
        {**base, "type": "assistant", "uuid": f"pytest-{session_id}-a1",
         "timestamp": "2026-06-19T00:00:01Z",
         "message": {"model": "claude-opus-4-8",
                     "content": [{"type": "tool_use", "id": f"pytest-{session_id}-t1",
                                  "name": "Edit", "input": {"file_path": f"{cwd}/x.py"}}]}},
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

    assert report1.events_created >= 3  # session_started + user_prompt + assistant_message + file_edit
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest capture/tests/test_cli_e2e.py -v`
Expected: FAIL — `ImportError`/`AttributeError` (`cli.process` not defined).

- [ ] **Step 4: Implement the CLI**

```python
# capture/claude_code/cli.py
"""Claude Code capture CLI: read transcripts, normalize, submit to the API."""

import argparse
import json
import os
import time
from pathlib import Path

import httpx

from capture.claude_code import client, identity, normalize
from capture.claude_code.discovery import discover_transcripts
from capture.claude_code.report import RunReport

DEFAULT_PROJECTS_DIR = "~/.claude/projects"


def load_lines(path: Path) -> list[dict]:
    """Parse a JSONL transcript, skipping blank/unparseable lines."""
    lines: list[dict] = []
    for raw in path.read_text(errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return lines


def events_for_file(path: Path, resolution: identity.RepoResolution, now: float) -> list[dict]:
    """Build POST-ready events for a registered transcript file."""
    lines = load_lines(path)
    if not lines:
        return []

    events: list[dict] = []
    events.extend(
        normalize.synthesize_session_events(lines[0], lines[-1], path.stat().st_mtime, now)
    )
    for line in lines:
        events.extend(normalize.normalize_line(line))

    # Determine a session-wide model so whichever event creates the run carries it.
    session_model = next((e["model"] for e in events if e.get("model")), None)

    ready: list[dict] = []
    for event in events:
        ready.append(
            {
                "tool": "claude-code",
                "project_id": resolution.project_id,
                "repository_id": resolution.repository_id,
                "session_id": event["session_id"],
                "event_type": event["event_type"],
                "model": session_model,
                "branch": event["branch"],
                "cwd": event["cwd"],
                "intent": None,
                "files_touched": event["files_touched"],
                "occurred_at": event["occurred_at"],
                "raw_payload": event["raw_payload"],
                "source_event_id": event["source_event_id"],
            }
        )
    return ready


def process(
    http: httpx.Client,
    api_url: str,
    files: list[Path],
    dry_run: bool = False,
    now: float | None = None,
) -> RunReport:
    """Resolve, normalize, and submit all files. Returns a RunReport."""
    now = now if now is not None else time.time()
    registry = identity.build_registry(client.fetch_repositories(http, api_url))
    report = RunReport()

    for path in files:
        lines = load_lines(path)
        cwd = next((line.get("cwd") for line in lines if line.get("cwd")), None)
        if cwd is None:
            report.files_skipped += 1
            report.add_local_only(path.parent.name)
            continue

        resolution = identity.resolve_repository(cwd, registry)
        if resolution.status == "pending":
            report.files_skipped += 1
            report.add_pending(resolution.canonical_remote)
            continue
        if resolution.status == "local_only":
            report.files_skipped += 1
            report.add_local_only(cwd)
            continue

        report.files_processed += 1
        for event in events_for_file(path, resolution, now):
            if dry_run:
                continue
            status, _ = client.post_event(http, api_url, event)
            if status == "created":
                report.events_created += 1
            elif status == "duplicate":
                report.events_duplicate += 1
            else:
                report.events_error += 1

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentOps Claude Code capture")
    parser.add_argument("--api-url", default=os.environ.get("AGENTOPS_API_URL", "http://localhost:8000"))
    parser.add_argument("--projects-dir", default=os.environ.get("CLAUDE_PROJECTS_DIR", DEFAULT_PROJECTS_DIR))
    parser.add_argument("--only", default=None, help="Substring filter on the project folder name")
    parser.add_argument("--since", type=float, default=None, help="Only files modified after this epoch time")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    files = discover_transcripts(args.projects_dir, only=args.only, since=args.since)
    with httpx.Client() as http:
        report = process(http, args.api_url, files, dry_run=args.dry_run)
    print(report.render())
    return 0
```

```python
# capture/claude_code/__main__.py
"""Entry point: python -m capture.claude_code"""

import sys

from capture.claude_code.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the end-to-end tests to verify they pass**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest capture/tests/test_cli_e2e.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Run the full suite + lint**

Run:
```bash
set -a; . ./.env; set +a
.venv/bin/ruff format app capture tests
.venv/bin/ruff check app capture tests
.venv/bin/pytest
```
Expected: ruff clean; all tests pass (existing + new).

- [ ] **Step 7: Commit**

```bash
git add capture/claude_code/cli.py capture/claude_code/__main__.py capture/tests/conftest.py capture/tests/test_cli_e2e.py
git commit -m "feat: wire Claude Code capture CLI end-to-end

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Docs + real backfill dry-run

**Files:**
- Modify: `README.md` (capture usage)
- Create: `docs/decisions/0003-claude-code-transcript-capture.md`

**Interfaces:** none (documentation + manual verification).

- [ ] **Step 1: Add the decision record**

```markdown
# docs/decisions/0003-claude-code-transcript-capture.md
# Decision 0003: Claude Code capture via transcript replay

## Status

Accepted

## Decision

Claude Code activity is captured by replaying the local JSONL session
transcripts (`~/.claude/projects/**/*.jsonl`) through the ingestion API, not via
hooks or OTEL in v1.

## Reason

- Transcripts are the only source that can backfill existing sessions and the
  only source carrying `cwd` + `gitBranch` (needed to attribute a session to a
  repository) and full prompt/response content.
- Per-line `uuid`s map to `run_events.source_event_id`, so replay is idempotent
  and safe across desktops.

## Identity

Repositories are keyed by canonical git remote URL (stable across machines),
resolved via `GET /repositories`. Unregistered remotes are quarantined to a
pending report; folders without a remote are skipped (opt-in only).

## Boundaries

v1 captures normalized meaningful events only. OTEL is a planned complementary
enrichment path (authoritative cost + real-time), joined on `session.id` /
`request_id`. See `docs/superpowers/specs/2026-06-19-claude-code-capture-design.md`.
```

- [ ] **Step 2: Add capture usage to README**

```markdown
<!-- README.md — append a new section after the Ingestion API section -->
## Claude Code capture

Replay local Claude Code transcripts into AgentOps Core (idempotent; safe to
re-run). The API must be running and the session's repository must be registered.

Preview without writing anything:

```bash
.venv/bin/python -m capture.claude_code --api-url http://localhost:8000 --dry-run
```

Ingest for real:

```bash
.venv/bin/python -m capture.claude_code --api-url http://localhost:8000
```

Useful flags: `--only <folder-substring>`, `--since <epoch>`,
`--projects-dir <path>`. Repositories that aren't registered yet are listed as
"pending" — add them to `database/seed.sql`, re-apply the seed, and re-run.
```

- [ ] **Step 3: Verify against real transcripts with a dry run**

Run:
```bash
set -a; . ./.env; set +a
.venv/bin/python -m capture.claude_code --api-url http://localhost:8000 --dry-run
```
Expected: a report listing processed files and any pending/local-only repos, with **no** DB writes. Confirm the `agentops-core` sessions resolve as registered and personal/unregistered folders appear under pending/local-only.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/decisions/0003-claude-code-transcript-capture.md
git commit -m "docs: document Claude Code capture usage + ADR 0003

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- §4 components → Tasks 2–9 (discovery, identity, normalize, client, report, cli). ✓
- §6 normalization mapping + source_event_id rules → Task 5 + Task 6. ✓
- §7 identity (GET /repositories, canonicalization, quarantine) → Tasks 1, 2, 3, and `process` in Task 9. ✓
- §8 idempotency/multi-desktop → Task 9 e2e re-run assertion. ✓
- §9 error handling (skip bad lines, retry transient, surface 4xx) → `load_lines` (Task 9), `post_event` (Task 7). ✓
- §10 token data in raw_payload, no usage_metrics, only read-only endpoint → assistant payload retained (Task 5); no usage writes anywhere. ✓
- §11 CLI flags (--api-url/--projects-dir/--only/--since/--dry-run, env) → Task 9 `main`. ✓
- §12 testing strategy → tests in every task; sanitized fixtures in Task 9. ✓
- §13 OTEL roadmap → documented (ADR 0003, Task 10); intentionally not built. ✓
- §14 scope guards → no hooks/OTEL/usage/commits/registration-write/Codex anywhere. ✓

**2. Placeholder scan:** No "TBD"/"add error handling"/"similar to". Every code step shows full code; every test step shows assertions. ✓

**3. Type consistency:** `RepoResolution(status, canonical_remote, project_id, repository_id)` used identically in Tasks 3 and 9. `normalize_line`/`synthesize_session_events`/`_event` keys (`event_type, session_id, cwd, branch, occurred_at, source_event_id, files_touched, raw_payload, model`) match what `events_for_file` reads. `post_event` returns `(status, http_status)` consumed in `process`. `fetch_repositories`→`build_registry`→`resolve_repository` chain is consistent. ✓
