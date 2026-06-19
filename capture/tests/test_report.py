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
