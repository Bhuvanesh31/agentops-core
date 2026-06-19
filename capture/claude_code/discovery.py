"""Find Claude Code transcript files under the projects directory."""

from pathlib import Path


def discover_transcripts(
    projects_dir: str,
    only: str | None = None,
    since: float | None = None,
) -> list[Path]:
    """Return sorted ``*/*.jsonl`` transcripts, filtered by slug and mtime."""
    base = Path(projects_dir).expanduser()
    files: list[Path] = []
    for path in sorted(base.glob("*/*.jsonl")):
        if only and only not in path.parent.name:
            continue
        if since is not None and path.stat().st_mtime < since:
            continue
        files.append(path)
    return files
