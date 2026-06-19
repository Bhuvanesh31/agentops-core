"""Repository identity resolution for transcripts.

Sessions are keyed to repositories by their canonical git remote URL, which is
stable across machines (unlike cwd). This module canonicalizes remotes and
resolves a working directory to a registered (project_id, repository_id).
"""

import re


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
