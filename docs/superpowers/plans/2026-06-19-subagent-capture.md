# Sub-agent Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture Claude Code sub-agent (Task/Agent-tool) transcripts by discovering the nested `subagents/` files and merging their events into the parent session's run.

**Architecture:** Sub-agents share the parent's `sessionId`, so their events land on the parent run via the existing `UNIQUE(tool_id, repository_id, session_id)` key. The change is confined to `capture/`: discovery finds the nested files; the CLI gives sub-agent files the parent session id and skips boundary synthesis; the report counts them. No schema change, no API change. Agent attribution stays queryable in `run_events.raw_payload`.

**Tech Stack:** Python 3.12, psycopg 3, httpx, pytest, ruff.

## Global Constraints

- Python `>=3.12`; psycopg 3 raw SQL, no ORM; reuse `app.redaction`; tool slug `"claude-code"`.
- A file is a **sub-agent file** iff its immediate parent directory is named `subagents`.
- Sub-agent transcripts: `<projects-dir>/<slug>/<parent-session-uuid>/subagents/agent-<id>.jsonl`. Their lines' `sessionId` equals `<parent-session-uuid>` (the directory above `subagents/`).
- Sub-agent files: use the **parent** session id and emit **no** `session_started`/`session_ended`. Top-level files keep current behavior (filename stem as session id, synthesized boundaries).
- Discovery must return **top-level (parent) files before sub-agent files**.
- No schema migration, no `parent_run_id`, no per-sub-agent runs (out of scope).
- ruff: `line-length = 100`, lint select `E, F, I, UP, B`.
- Tests run against the live Postgres container; create rows with ids prefixed `pytest-`; sanitized synthetic transcripts only.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Branch: `feat/subagent-capture`.

---

### Task 1: Discover sub-agent transcripts

**Files:**
- Modify: `capture/claude_code/discovery.py`
- Test: `capture/tests/test_discovery.py`

**Interfaces:**
- Produces: `discover_transcripts(projects_dir, only=None, since=None) -> list[Path]` now also returns `*/*/subagents/*.jsonl` files, ordered parents-before-children, with `only` matched against the **slug** (first path part under the base), not the immediate parent dir.

- [ ] **Step 1: Write the failing tests**

```python
# capture/tests/test_discovery.py — append these tests

def test_discovers_nested_subagent_files(tmp_path):
    (tmp_path / "slug").mkdir()
    (tmp_path / "slug" / "s1.jsonl").write_text("{}\n")
    sub = tmp_path / "slug" / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-x.jsonl").write_text("{}\n")
    found = discover_transcripts(str(tmp_path))
    names = [p.name for p in found]
    assert "s1.jsonl" in names
    assert "agent-x.jsonl" in names
    # parent (top-level) comes before its sub-agent child
    assert names.index("s1.jsonl") < names.index("agent-x.jsonl")


def test_only_filter_matches_slug_for_subagent_files(tmp_path):
    sub_a = tmp_path / "proj-a" / "s1" / "subagents"
    sub_a.mkdir(parents=True)
    (sub_a / "agent-a.jsonl").write_text("{}\n")
    sub_b = tmp_path / "proj-b" / "s2" / "subagents"
    sub_b.mkdir(parents=True)
    (sub_b / "agent-b.jsonl").write_text("{}\n")
    found = discover_transcripts(str(tmp_path), only="proj-b")
    assert [p.name for p in found] == ["agent-b.jsonl"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest capture/tests/test_discovery.py -v`
Expected: FAIL — sub-agent files are not discovered (nested glob missing).

- [ ] **Step 3: Implement dual-glob discovery**

```python
# capture/claude_code/discovery.py — replace the whole function body

def discover_transcripts(
    projects_dir: str,
    only: str | None = None,
    since: float | None = None,
) -> list[Path]:
    """Return transcripts, parents before sub-agent children, filtered.

    Matches top-level ``<slug>/<file>.jsonl`` and nested sub-agent
    ``<slug>/<session>/subagents/<file>.jsonl``. ``only`` is matched against the
    slug (first path part); ``since`` is an mtime floor (epoch seconds).
    """
    base = Path(projects_dir).expanduser()
    matches = list(base.glob("*/*.jsonl")) + list(base.glob("*/*/subagents/*.jsonl"))
    # Shallower paths (parents) first, then lexical, so a parent file is always
    # processed before its sub-agent children.
    matches.sort(key=lambda p: (len(p.relative_to(base).parts), str(p)))

    files: list[Path] = []
    for path in matches:
        slug = path.relative_to(base).parts[0]
        if only and only not in slug:
            continue
        if since is not None and path.stat().st_mtime < since:
            continue
        files.append(path)
    return files
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest capture/tests/test_discovery.py -v`
Expected: PASS (new tests + the 3 existing discovery tests still green).

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format capture
.venv/bin/ruff check capture
git add capture/claude_code/discovery.py capture/tests/test_discovery.py
git commit -m "feat: discover nested sub-agent transcripts

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Sub-agent identity in events_for_file

**Files:**
- Modify: `capture/claude_code/cli.py`
- Test: `capture/tests/test_cli_e2e.py`

**Interfaces:**
- Produces:
  - `cli.is_subagent_file(path: Path) -> bool` — True iff `path.parent.name == "subagents"`.
  - `cli.events_for_file(path, lines, resolution, now)` — for a sub-agent file, emits **no** synthesized `session_started`/`session_ended`; each event keeps the line's `sessionId` (the parent session). Top-level behavior unchanged.

- [ ] **Step 1: Write the failing test**

```python
# capture/tests/test_cli_e2e.py — append

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest capture/tests/test_cli_e2e.py::test_events_for_file_subagent_skips_boundaries_and_uses_parent_session -v`
Expected: FAIL — `is_subagent_file` undefined / `session_started` present.

- [ ] **Step 3: Add the helper**

```python
# capture/claude_code/cli.py — add after the DEFAULT_PROJECTS_DIR constant
def is_subagent_file(path: Path) -> bool:
    """A transcript is a sub-agent file iff its parent dir is named 'subagents'."""
    return path.parent.name == "subagents"
```

- [ ] **Step 4: Branch events_for_file on sub-agent files**

Replace the event-collection section of `events_for_file` (the block from `# The transcript filename is the canonical session id` through the `for line in lines: events.extend(normalize.normalize_line(line))` loop) with:

```python
    # Sub-agent transcripts share the parent's sessionId (carried on every
    # line), so their normalized events attach to the parent run. The parent
    # file owns the session boundaries, so we synthesize none here.
    events: list[dict] = []
    if not is_subagent_file(path):
        # The filename is the canonical session id; boundary lines may lack a
        # sessionId, so derive it from the path rather than from a line.
        session_id = path.stem
        events.extend(
            normalize.synthesize_session_events(
                session_id, lines[0], lines[-1], path.stat().st_mtime, now
            )
        )
    for line in lines:
        events.extend(normalize.normalize_line(line))
```

(The rest of `events_for_file` — the session-wide model/cwd/branch computation and the `ready` list build — is unchanged.)

- [ ] **Step 5: Run test to verify it passes**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest capture/tests/test_cli_e2e.py::test_events_for_file_subagent_skips_boundaries_and_uses_parent_session -v`
Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff format capture
.venv/bin/ruff check capture
git add capture/claude_code/cli.py capture/tests/test_cli_e2e.py
git commit -m "feat: sub-agent files use parent session id, skip boundaries

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Report counter + process routing + merge e2e

**Files:**
- Modify: `capture/claude_code/report.py`
- Modify: `capture/claude_code/cli.py` (`process`)
- Test: `capture/tests/test_report.py`, `capture/tests/test_cli_e2e.py`

**Interfaces:**
- Consumes: `cli.is_subagent_file` (Task 2).
- Produces:
  - `RunReport.sub_agent_files: int` counter, shown in `render()`.
  - `process(...)` increments `sub_agent_files` for sub-agent files and otherwise counts `files_processed` / `files_catch_all` as before.

- [ ] **Step 1: Write the failing report test**

```python
# capture/tests/test_report.py — append

def test_render_shows_sub_agent_count():
    r = RunReport()
    r.files_processed = 2
    r.sub_agent_files = 5
    text = r.render()
    assert "sub_agents=5" in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest capture/tests/test_report.py::test_render_shows_sub_agent_count -v`
Expected: FAIL — `sub_agent_files` attribute / `sub_agents=` text missing.

- [ ] **Step 3: Add the counter and render it**

```python
# capture/claude_code/report.py — add the field after files_catch_all
    sub_agent_files: int = 0
```
```python
# capture/claude_code/report.py — replace the "files:" render line
            (
                f"files: processed={self.files_processed} "
                f"sub_agents={self.sub_agent_files} "
                f"catch_all={self.files_catch_all} skipped={self.files_skipped}"
            ),
```

- [ ] **Step 4: Run the report test to verify it passes**

Run: `.venv/bin/pytest capture/tests/test_report.py -v`
Expected: PASS (new test + existing report tests).

- [ ] **Step 5: Refactor process counting**

In `capture/claude_code/cli.py`, replace the resolution-and-counting block in `process` (from `resolution = identity.resolve_repository(cwd, registry)` through the `else:  # local_only, no catch-all configured` branch, i.e. up to but not including the `for event in events_for_file(...)` loop) with:

```python
        is_sub = is_subagent_file(path)
        resolution = identity.resolve_repository(cwd, registry)
        if resolution.status == "pending":
            report.files_skipped += 1
            report.add_pending(resolution.canonical_remote)
            continue

        routed_catch_all = False
        if resolution.status == "local_only":
            if catch_all_ids is None:
                report.files_skipped += 1
                report.add_local_only(cwd)
                continue
            resolution = identity.RepoResolution(
                "registered", None, catch_all_ids[0], catch_all_ids[1]
            )
            routed_catch_all = True

        if is_sub:
            report.sub_agent_files += 1
        elif routed_catch_all:
            report.files_catch_all += 1
            report.add_catch_all(cwd)
        else:
            report.files_processed += 1
```

- [ ] **Step 6: Write the failing merge e2e test**

```python
# capture/tests/test_cli_e2e.py — append

def test_subagent_events_merge_into_parent_run(asgi_client, tmp_path):
    cwd = _repo_root()  # origin matches the seeded agentops-core-main repo
    parent = f"pytest-session-{uuid4().hex}"
    slug = "-".join(cwd.strip("/").split("/"))

    pdir = tmp_path / slug
    pdir.mkdir(parents=True)
    parent_lines = [
        {"type": "user", "sessionId": parent, "cwd": cwd, "gitBranch": "main",
         "uuid": f"{parent}-u", "timestamp": "2026-06-19T00:00:00Z",
         "message": {"content": "go"}},
        {"type": "assistant", "sessionId": parent, "cwd": cwd, "gitBranch": "main",
         "uuid": f"{parent}-a", "timestamp": "2026-06-19T00:00:01Z",
         "message": {"model": "claude-opus-4-8", "content": [{"type": "text", "text": "ok"}]}},
    ]
    (pdir / f"{parent}.jsonl").write_text("\n".join(json.dumps(x) for x in parent_lines) + "\n")

    sdir = pdir / parent / "subagents"
    sdir.mkdir(parents=True)
    sub_line = {"type": "assistant", "sessionId": parent, "agentId": "agentX",
                "isSidechain": True, "attributionAgent": "Explore", "cwd": cwd,
                "gitBranch": "main", "uuid": f"{parent}-sa",
                "timestamp": "2026-06-19T00:00:02Z",
                "message": {"model": "claude-opus-4-8",
                            "content": [{"type": "text", "text": "searched"}]}}
    (sdir / "agent-x.jsonl").write_text(json.dumps(sub_line) + "\n")

    from capture.claude_code.discovery import discover_transcripts

    files = discover_transcripts(str(tmp_path))
    report = cli.process(asgi_client, "http://test", files, now=10_000_000_000.0)

    assert report.sub_agent_files == 1
    assert report.events_error == 0

    with get_connection() as conn:
        runs = conn.execute(
            "SELECT run_id FROM runs WHERE session_id = %s", (parent,)
        ).fetchall()
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
```

- [ ] **Step 7: Run the suite to verify it passes**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest -p no:warnings`
Expected: PASS (all tests, including the new merge e2e).

- [ ] **Step 8: Lint and commit**

```bash
.venv/bin/ruff format app capture tests
.venv/bin/ruff check app capture tests
git add capture/claude_code/report.py capture/claude_code/cli.py capture/tests/test_report.py capture/tests/test_cli_e2e.py
git commit -m "feat: merge sub-agent events into parent run; report count

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Docs + real sub-agent extraction

**Files:**
- Modify: `README.md`
- Create: `docs/decisions/0004-subagent-capture.md`

**Interfaces:** none (documentation + manual verification).

- [ ] **Step 1: Add the decision record**

```markdown
# docs/decisions/0004-subagent-capture.md
# Decision 0004: Sub-agent capture merges into the parent run

## Status

Accepted

## Decision

Claude Code sub-agent (Task/Agent-tool) transcripts — stored at
`<slug>/<parent-session>/subagents/agent-*.jsonl` — are discovered and their
events merged into the parent session's run. Sub-agents share the parent's
`sessionId`, so the existing run key attaches them automatically.

## Reason

- Sub-agents are part of one Claude Code session, not separate sessions; merging
  keeps a run's total cost/tokens complete.
- Agent attribution (`agentId`, `attributionAgent`, `isSidechain`) is preserved
  in `run_events.raw_payload` and is queryable via the existing GIN index, so no
  schema change is needed in the capture phase.

## Boundaries

No `parent_run_id`, no per-sub-agent runs, no schema migration. Promoting agent
attribution to columns or a parent→child tree is deferred to the registry /
insights milestone. See
`docs/superpowers/specs/2026-06-19-subagent-capture-design.md`.
```

- [ ] **Step 2: Note sub-agent capture in the README**

```markdown
<!-- README.md — append to the "Claude Code capture" section -->
Sub-agent (Task/Agent-tool) sessions are captured automatically and merged into
their parent session's run. Slice sub-agent activity with
`raw_payload->>'isSidechain' = 'true'`, grouped by `raw_payload->>'attributionAgent'`.
```

- [ ] **Step 3: Run a real extraction including sub-agents**

Run:
```bash
set -a; . ./.env; set +a
.venv/bin/python -m capture.claude_code --api-url http://localhost:8000 --catch-all unsorted-local --exclude personal
```
Expected: the report's `files:` line now shows a non-zero `sub_agents=` count; `error=0`.

- [ ] **Step 4: Verify sub-agent events landed and merged**

Run:
```bash
set -a; . ./.env; set +a
docker exec agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT 'sidechain_events='||count(*) FROM run_events WHERE raw_payload->>'isSidechain'='true';
   SELECT 'agent_types='||count(DISTINCT raw_payload->>'attributionAgent') FROM run_events WHERE raw_payload->>'attributionAgent' IS NOT NULL;"
```
Expected: `sidechain_events` > 0; sub-agent events share runs with their parents (no orphan runs created for `agent-*` filenames — runs are keyed by parent session id).

- [ ] **Step 5: Commit the docs**

```bash
git add README.md docs/decisions/0004-subagent-capture.md
git commit -m "docs: document sub-agent capture + ADR 0004

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- §5.1 discovery (nested glob, slug `only`, parents-first) → Task 1. ✓
- §5.2 identity (parent session id, no boundary synthesis, `subagents`-dir detection) → Task 2. ✓
- §5.3 report `sub_agent_files` counter + render → Task 3. ✓
- §6 data flow / §7 ordering (parents first via discovery sort; get_or_create_run attaches) → Task 1 ordering + Task 3 e2e (one run). ✓
- §8 querying (`raw_payload->>'isSidechain'`/`attributionAgent`) → Task 3 e2e assertion + Task 4 verification. ✓
- §9 testing (discovery, events_for_file unit, merge e2e, report count) → Tasks 1–3 tests. ✓
- §10 scope (no schema/parent_run_id) → no migration in any task. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step has complete code; every test step has assertions. ✓

**3. Type consistency:** `is_subagent_file(path) -> bool` defined in Task 2, consumed in Task 3. `RunReport.sub_agent_files` added in Task 3 and rendered there. `RepoResolution("registered", None, project_id, repository_id)` matches the frozen dataclass field order used elsewhere. `events_for_file(path, lines, resolution, now)` signature unchanged. ✓
