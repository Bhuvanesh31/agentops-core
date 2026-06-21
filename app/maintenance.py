"""Host-local maintenance commands for the AgentOps service database.

Run with: python -m app.maintenance
Currently: recompute runs.started_at/ended_at from event times (backfill).
"""

import argparse

from app.database import get_connection
from app.models.ingestion import backfill_run_time_bounds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recompute runs.started_at/ended_at from run_events.occurred_at"
    )
    parser.parse_args(argv)

    with get_connection() as conn:
        changed = backfill_run_time_bounds(conn)
    print(f"runs updated: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
