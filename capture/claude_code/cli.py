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


def events_for_file(
    path: Path, lines: list[dict], resolution: identity.RepoResolution, now: float
) -> list[dict]:
    """Build POST-ready events for a registered transcript file."""
    if not lines:
        return []

    # The transcript filename is the canonical session id; boundary lines may
    # lack a sessionId, so derive it from the path rather than from a line.
    session_id = path.stem
    events: list[dict] = []
    events.extend(
        normalize.synthesize_session_events(
            session_id, lines[0], lines[-1], path.stat().st_mtime, now
        )
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
    catch_all: str | None = None,
    exclude: list[str] | None = None,
) -> RunReport:
    """Resolve, normalize, and submit all files. Returns a RunReport.

    ``catch_all`` is a registered repository_id; sessions whose cwd has no git
    remote are routed there (real folder preserved in each event's cwd) instead
    of being skipped. ``exclude`` drops sessions whose cwd contains any of the
    given substrings (e.g. personal folders).
    """
    now = now if now is not None else time.time()
    repos = client.fetch_repositories(http, api_url)
    registry = identity.build_registry(repos)
    exclude = exclude or []

    catch_all_ids: tuple[str, str] | None = None
    if catch_all:
        match = next((r for r in repos if r["repository_id"] == catch_all), None)
        if match is None:
            raise ValueError(f"--catch-all repository not registered: {catch_all}")
        catch_all_ids = (match["project_id"], match["repository_id"])

    report = RunReport()

    for path in files:
        lines = load_lines(path)
        cwd = next((line.get("cwd") for line in lines if line.get("cwd")), None)
        if cwd is None:
            report.files_skipped += 1
            report.add_local_only(path.parent.name)
            continue
        if any(token in cwd for token in exclude):
            report.files_skipped += 1
            report.add_excluded(cwd)
            continue

        resolution = identity.resolve_repository(cwd, registry)
        if resolution.status == "registered":
            report.files_processed += 1
        elif resolution.status == "pending":
            report.files_skipped += 1
            report.add_pending(resolution.canonical_remote)
            continue
        elif catch_all_ids is not None:  # local_only, routed to catch-all
            resolution = identity.RepoResolution(
                "registered", None, catch_all_ids[0], catch_all_ids[1]
            )
            report.files_catch_all += 1
            report.add_catch_all(cwd)
        else:  # local_only, no catch-all configured
            report.files_skipped += 1
            report.add_local_only(cwd)
            continue

        for event in events_for_file(path, lines, resolution, now):
            if dry_run:
                continue
            try:
                status, _ = client.post_event(http, api_url, event)
            except RuntimeError:
                report.events_error += 1
                continue
            if status == "created":
                report.events_created += 1
            elif status == "duplicate":
                report.events_duplicate += 1
            else:
                report.events_error += 1

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentOps Claude Code capture")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("AGENTOPS_API_URL", "http://localhost:8000"),
    )
    parser.add_argument(
        "--projects-dir",
        default=os.environ.get("CLAUDE_PROJECTS_DIR", DEFAULT_PROJECTS_DIR),
    )
    parser.add_argument("--only", default=None, help="Substring filter on the project folder name")
    parser.add_argument(
        "--since", type=float, default=None, help="Only files modified after this epoch time"
    )
    parser.add_argument(
        "--catch-all",
        default=os.environ.get("AGENTOPS_CATCH_ALL_REPO"),
        help="Registered repository_id to route no-remote sessions to (e.g. unsorted-local)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Skip sessions whose cwd contains this substring (repeatable)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    files = discover_transcripts(args.projects_dir, only=args.only, since=args.since)
    with httpx.Client() as http:
        report = process(
            http,
            args.api_url,
            files,
            dry_run=args.dry_run,
            catch_all=args.catch_all,
            exclude=args.exclude,
        )
    print(report.render())
    return 0
