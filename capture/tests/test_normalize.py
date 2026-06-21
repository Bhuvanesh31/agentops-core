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
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Edit",
                    "input": {"file_path": "/repo/x.py"},
                },
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
            "content": [
                {"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {"command": "ls"}}
            ],
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
    for noise in [
        "attachment",
        "mode",
        "permission-mode",
        "ai-title",
        "system",
        "file-history-snapshot",
        "last-prompt",
        "bridge-session",
    ]:
        assert normalize_line({**BASE, "type": noise, "uuid": "n"}) == []


def test_assistant_unknown_tool_yields_tool_use():
    line = {
        **BASE,
        "type": "assistant",
        "uuid": "a4",
        "message": {
            "model": "claude-opus-4-8",
            "content": [
                {"type": "tool_use", "id": "toolu_3", "name": "WebSearch", "input": {"q": "x"}}
            ],
        },
    }
    events = normalize_line(line)
    assert events[1]["event_type"] == "tool_use"
    assert events[1]["source_event_id"] == "toolu_3"


def test_assistant_write_tool_yields_file_edit():
    line = {
        **BASE,
        "type": "assistant",
        "uuid": "a5",
        "message": {
            "model": "claude-opus-4-8",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_4",
                    "name": "Write",
                    "input": {"file_path": "/repo/y.py"},
                }
            ],
        },
    }
    events = normalize_line(line)
    assert events[1]["event_type"] == "file_edit"
    assert events[1]["files_touched"] == ["/repo/y.py"]


def test_secret_in_user_prompt_is_redacted():
    line = {
        **BASE,
        "type": "user",
        "uuid": "u9",
        "message": {"content": "key sk-ant-api03-aaaaaaaaaaaaaaaaaaaa"},
    }
    events = normalize_line(line)
    import json

    assert "sk-ant-api03" not in json.dumps(events[0]["raw_payload"])
