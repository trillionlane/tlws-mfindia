"""Fill NULL fund_facts risk/return fields from our own NAV series.

    PYTHONPATH=src python scripts/compute_metrics.py                # all in-scope live funds
    PYTHONPATH=src python scripts/compute_metrics.py --dry-run      # report only, no writes
    PYTHONPATH=src python scripts/compute_metrics.py --codes 108273 # just one fund

Fill-if-missing only: a field that already has a value is never touched.
Every fill is recorded in mf.computed_fields_log (method, window, as-of).
Idempotent: re-running fills nothing new (unless more NAVs landed).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.jobs.compute_metrics import MIN_POINTS, compute_fund_metrics
from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn
from mfdataindia.store.postgres import PostgresStore


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None,
                    help="target DSN (default: $MFDATAINDIA_DSN); required -- there is "
                         "no built-in default")
    ap.add_argument("--codes", default=None,
                    help="comma-separated amfi_scheme_code list (default: all in-scope live)")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute and report what would be filled; write nothing")
    ap.add_argument("--max-funds", type=int, default=None)
    ap.add_argument("--min-points", type=int, default=MIN_POINTS,
                    help=f"skip funds with fewer NAV points (default {MIN_POINTS})")
    args = ap.parse_args()

    try:
        dsn = resolve_dsn(args.dsn, purpose="the computed-metrics fill")
    except DsnError as exc:
        ap.error(str(exc))
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("compute_metrics").info("target database: %s", describe_dsn(dsn))
    store = PostgresStore(dsn)
    codes = [int(c) for c in args.codes.split(",")] if args.codes else None
    with store:
        rep = compute_fund_metrics(store, codes=codes, dry_run=args.dry_run,
                                   max_funds=args.max_funds,
                                   min_points=args.min_points)
    print("COMPUTE REPORT:", rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())