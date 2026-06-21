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
    assert "(2 sessions)" in text
    assert "/home/me/scratch" in text


def test_pending_counts_accumulate():
    r = RunReport()
    r.add_pending("a")
    r.add_pending("a")
    r.add_pending("b")
    assert r.pending_repos == {"a": 2, "b": 1}


def test_render_shows_sub_agent_count():
    r = RunReport()
    r.files_processed = 2
    r.sub_agent_files = 5
    text = r.render()
    assert "sub_agents=5" in text
