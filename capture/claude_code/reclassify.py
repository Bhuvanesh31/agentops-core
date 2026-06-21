"""Reclassify catch-all runs into their real repositories via the cwd map.

Host-local maintenance command: connects directly to Postgres (not via the
HTTP API) and re-points runs whose recorded cwd matches the override map off
the catch-all repository. Events follow their run (no event rewrite).
"""

import argparse

import psycopg

from app.database import get_connection
from app.models import ingestion
from capture.claude_code.overrides import load_overrides

CATCH_ALL_REPOSITORY_ID = "unsorted-local"


def reclassify(conn: psycopg.Connection, overrides: dict[str, str]) -> dict[str, int]:
    """Move catch-all runs to their mapped repos. Returns {repository_id: rows_moved}.

    For each cwd -> repository_id, runs currently in the catch-all whose cwd
    equals the mapped cwd or is a path-segment child of it are re-pointed to the
    repository and its project. Unregistered target repos are skipped.
    """
    moved: dict[str, int] = {}
    for cwd, repository_id in overrides.items():
        repo = ingestion.get_repository(conn, repository_id)
        if repo is None:
            continue
        # Escape LIKE metacharacters so real cwds containing `_` or `%` match literally.
        like_prefix = cwd.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
        result = conn.execute(
            """
            UPDATE runs
            SET repository_id = %s, project_id = %s, updated_at = NOW()
            WHERE repository_id = %s
              AND (cwd = %s OR cwd LIKE %s ESCAPE '\\')
            """,
            (repository_id, repo["project_id"], CATCH_ALL_REPOSITORY_ID, cwd, like_prefix),
        )
        moved[repository_id] = moved.get(repository_id, 0) + result.rowcount
    return moved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reclassify catch-all runs into real repositories via the cwd map"
    )
    parser.add_argument(
        "--cwd-map",
        default=None,
        help="Path to cwd_overrides.toml (default: the bundled map)",
    )
    args = parser.parse_args(argv)

    overrides = load_overrides(args.cwd_map)
    with get_connection() as conn:
        moved = reclassify(conn, overrides)

    for repository_id, count in sorted(moved.items()):
        print(f"{repository_id}: {count}")
    print(f"total moved: {sum(moved.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
