"""Host-local maintenance commands for the AgentOps service database.

Usage:
  python -m app.maintenance backfill-run-times   # runs.started_at/ended_at from event times
  python -m app.maintenance backfill-usage       # token usage from run_events -> usage_metrics
"""

import argparse

from app.database import get_connection
from app.models.ingestion import backfill_run_time_bounds, backfill_usage_metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentOps service maintenance commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "backfill-run-times",
        help="Recompute runs.started_at/ended_at from run_events.occurred_at",
    )
    sub.add_parser(
        "backfill-usage",
        help="Aggregate run_events token usage into usage_metrics",
    )
    args = parser.parse_args(argv)

    with get_connection() as conn:
        if args.command == "backfill-run-times":
            print(f"runs updated: {backfill_run_time_bounds(conn)}")
        elif args.command == "backfill-usage":
            print(f"usage rows written: {backfill_usage_metrics(conn)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
