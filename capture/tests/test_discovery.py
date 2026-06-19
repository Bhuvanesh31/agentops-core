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
