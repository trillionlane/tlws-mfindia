#!/usr/bin/env python3
"""Assess NAV-integrity detection signals across the complete database.

Runs the governed NAV-integrity audit (mfdataindia.audit.nav_integrity) over
the FULL NAV history — not a sample, not a known-code list — and refreshes
``mf.nav_quality_assessments``. Deterministic and idempotent: the same
database yields the same signals and the same assessment rows on every run.

Safety:

* read-only over ``mf.nav_history`` — the audit never mutates stored NAVs;
* its only write target is ``mf.nav_quality_assessments``;
* ``--dry-run`` reports without writing anything;
* the write is one transaction (all-or-nothing).

Usage:

    PYTHONPATH=src python scripts/audit_nav_integrity.py            # assess + persist
    PYTHONPATH=src python scripts/audit_nav_integrity.py --dry-run  # report only
    ... --dsn 'postgresql://...'                                    # explicit database

The JSON summary (methodology version, dataset version, counts, affected
codes) goes to stdout; progress goes to stderr. Scheduling: this is a
bounded operator run, NOT part of the six-hour NAV refresh, until its
runtime is benchmarked and scheduling is separately approved.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.audit import nav_integrity  # noqa: E402
from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402

log = logging.getLogger("audit_nav_integrity")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="database DSN (default: $MFDATAINDIA_DSN)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="assess and report without writing mf.nav_quality_assessments",
    )
    args = parser.parse_args()

    try:
        dsn = resolve_dsn(args.dsn, purpose="the NAV-integrity audit")
    except DsnError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log.info("target database: %s", describe_dsn(dsn))
    log.info(
        "scanning the complete NAV history; this is a bounded full-history "
        "audit, not a request-path query"
    )

    started = perf_counter()
    with PostgresStore(dsn, use_copy=False, connect_timeout=30) as store:
        with store.transaction() as conn:
            # Keep the full-history scan, dataset version, and persisted
            # assessments on one stable snapshot if a daily refresh overlaps
            # this separately scheduled operator run. A later refresh then
            # advances dataset_version and makes these assessments stale.
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            summary = nav_integrity.assess(conn, write=not args.dry_run)
    summary["duration_ms"] = int((perf_counter() - started) * 1000)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
