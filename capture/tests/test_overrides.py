"""Unit tests for the cwd override map loader and longest-prefix matcher."""

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
