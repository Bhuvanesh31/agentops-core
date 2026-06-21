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
        [
            {
                "repository_id": "r1",
                "project_id": "p1",
                "remote_url": "git@github.com:acme/widget.git",
            }
        ]
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


def test_resolve_uses_override_map_when_no_remote(tmp_path):
    # tmp_path is not a git repo -> no remote -> override map should resolve it.
    overrides = {str(tmp_path): ("proj-x", "repo-x")}
    res = resolve_repository(str(tmp_path), {}, overrides)
    assert res == RepoResolution("registered", None, "proj-x", "repo-x")


def test_resolve_local_only_when_no_remote_and_not_in_overrides(tmp_path):
    res = resolve_repository(str(tmp_path), {}, {"/other/path": ("p", "r")})
    assert res.status == "local_only"
