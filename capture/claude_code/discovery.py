"""Find Claude Code transcript files under the projects directory."""

from pathlib import Path


def discover_transcripts(
    projects_dir: str,
    only: str | None = None,
    since: float | None = None,
) -> list[Path]:
    """Return transcripts, parents before sub-agent children, filtered.

    Matches top-level ``<slug>/<file>.jsonl`` and nested sub-agent
    ``<slug>/<session>/subagents/<file>.jsonl``. ``only`` is matched against the
    slug (first path part); ``since`` is an mtime floor (epoch seconds).
    """
    base = Path(projects_dir).expanduser()
    matches = list(base.glob("*/*.jsonl")) + list(base.glob("*/*/subagents/*.jsonl"))
    # Shallower paths (parents) first, then lexical, so a parent file is always
    # processed before its sub-agent children.
    matches.sort(key=lambda p: (len(p.relative_to(base).parts), str(p)))

    files: list[Path] = []
    for path in matches:
        slug = path.relative_to(base).parts[0]
        if only and only not in slug:
            continue
        if since is not None and path.stat().st_mtime < since:
            continue
        files.append(path)
    return files
