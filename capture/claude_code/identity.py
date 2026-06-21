"""Repository identity resolution for transcripts.

Sessions are keyed to repositories by their canonical git remote URL, which is
stable across machines (unlike cwd). This module canonicalizes remotes and
resolves a working directory to a registered (project_id, repository_id).
"""

import re
import subprocess
from dataclasses import dataclass

from capture.claude_code.overrides import match_override


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


@dataclass(frozen=True)
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
        project_id = repo.get("project_id")
        repository_id = repo.get("repository_id")
        if canon and project_id and repository_id:
            registry[canon] = (project_id, repository_id)
    return registry


def resolve_repository(
    cwd: str,
    registry: dict[str, tuple[str, str]],
    overrides: dict[str, tuple[str, str]] | None = None,
) -> RepoResolution:
    """Resolve a transcript's cwd to a repository.

    Order: git remote -> registry (registered/pending). If the cwd has no
    remote, fall back to the cwd override map (longest-prefix) before declaring
    it local_only. ``overrides`` maps cwd -> (project_id, repository_id).
    """
    canon = canonicalize_remote(get_git_remote(cwd))
    if canon is None:
        if overrides:
            hit = match_override(cwd, overrides)
            if hit:
                return RepoResolution("registered", None, hit[0], hit[1])
        return RepoResolution("local_only", None)
    hit = registry.get(canon)
    if hit:
        return RepoResolution("registered", canon, hit[0], hit[1])
    return RepoResolution("pending", canon)
