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
        if (
            not isinstance(content, str)
            or line.get("isMeta")
            or line.get("toolUseResult") is not None
        ):
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


def synthesize_session_events(
    session_id: str,
    first_line: dict,
    last_line: dict,
    file_mtime: float,
    now: float,
    idle_seconds: float = 3600,
) -> list[dict[str, Any]]:
    """Synthesize session_started (always) and session_ended (only when idle).

    ``session_id`` is the file's canonical session id (the transcript filename),
    passed explicitly because boundary lines are not guaranteed to carry a
    ``sessionId`` — the last line of a transcript can be a metadata line with no
    session field, which would otherwise yield a null session_id and a rejected
    event.
    """
    started = _event("session_started", first_line, f"{session_id}#session_started", {})
    started["session_id"] = session_id
    events = [started]
    if now - file_mtime >= idle_seconds:
        ended = _event("session_ended", last_line, f"{session_id}#session_ended", {})
        ended["session_id"] = session_id
        ended["cwd"] = ended["cwd"] or first_line.get("cwd")
        ended["branch"] = ended["branch"] or first_line.get("gitBranch")
        events.append(ended)
    return events
