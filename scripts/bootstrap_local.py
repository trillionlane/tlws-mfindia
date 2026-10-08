"""Populate the local MFDataIndia database.

Order of operations (each step is idempotent, so re-running is safe):

1. Fetch today's ``NAVAll.txt`` and load funds / variants / quality flags.
2. Backfill NAV history for ``--years`` back from today (resumable via
   ``mf.ingest_checkpoints``).

Usage::

    PYTHONPATH=src python scripts/bootstrap_local.py --years 1
    PYTHONPATH=src python scripts/bootstrap_local.py --years 5 --min-delay 1.0

Environment:
    MFDATAINDIA_DSN   Postgres DSN (required; no built-in default -- see store/dsn.py)
    MF_TEST_NO_COPY   set to 1 to force batched INSERT instead of COPY bulk load
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.ingest.amfi_client import AmfiClient
from mfdataindia.ingest.amfi_navall import parse_navall
from mfdataindia.jobs.backfill_nav import backfill_nav_history
from mfdataindia.load.amfi_to_store import load_parsed_amfi
from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn
from mfdataindia.store.postgres import PostgresStore

log = logging.getLogger("bootstrap")


def main() -> int:
    ap = argparse.ArgumentParser(description="Bootstrap the local MFDataIndia database")
    ap.add_argument("--years", type=float, default=1.0,
                    help="NAV-history window to backfill (default 1 year for a fast demo)")
    ap.add_argument("--chunk-days", type=int, default=90)
    ap.add_argument("--min-delay", type=float, default=1.0,
                    help="seconds between AMFI requests")
    ap.add_argument("--dsn", default=os.environ.get("MFDATAINDIA_DSN"),
                    help="target DSN (default: $MFDATAINDIA_DSN; required -- there is "
                         "no built-in default; see store/dsn.py)")
    ap.add_argument("--use-copy", action="store_true",
                    help="force COPY bulk load (default unless MF_TEST_NO_COPY=1)")
    ap.add_argument("--skip-history", action="store_true",
                    help="load only the latest snapshot, no backfill")
    ap.add_argument("--force", action="store_true",
                    help="reset AMFI_HISTORY checkpoints first, forcing a full re-backfill "
                         "(use if nav_history was wiped independently of the checkpoints)")
    args = ap.parse_args()

    try:
        dsn = resolve_dsn(args.dsn, allow_pglite=False, purpose="the local bootstrap")
    except DsnError as exc:
        ap.error(str(exc))
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log.info("target database: %s", describe_dsn(dsn))
    today = date.today()
    use_copy = args.use_copy if args.use_copy else (os.environ.get("MF_TEST_NO_COPY") is None)

    client = AmfiClient(min_delay=args.min_delay)
    store = PostgresStore(dsn, use_copy=use_copy)

    log.info("=== step 1: NAVAll.txt snapshot ===")
    fetch = client.fetch_navall()
    schemes, report = parse_navall(fetch.text)
    log.info("parsed %d schemes (format %s)", len(schemes), report.format)
    with store:
        load_parsed_amfi(store, schemes, source_date=today)
        cov = store.coverage()
        log.info("funds=%d in_scope_live=%d amcs=%d",
                 cov["schemes_total"], cov["in_scope_live"], cov["amcs"])

        if not args.skip_history:
            if args.force:
                with store.transaction() as conn, conn.cursor() as cur:
                    n = cur.execute(
                        "DELETE FROM mf.ingest_checkpoints "
                        "WHERE source='AMFI_HISTORY' AND entity_kind='NAV_HISTORY'"
                    ).rowcount
                log.info("--force: reset %d backfill checkpoints", n)
            log.info("=== step 2: NAV-history backfill (%s years) ===", args.years)
            from_date = today - timedelta(days=int(args.years * 365.25))
            rep = backfill_nav_history(
                store, client, from_date, today,
                chunk_days=args.chunk_days, strict=False)
            log.info("backfill: %s", rep.as_dict())
            log.info("nav_span: %s", store.nav_span())

        # backfill_nav_history refreshes this itself; the explicit refresh also
        # covers --skip-history and gives the completed bootstrap one version.
        store.refresh_dataset_summary("local_bootstrap")

    log.info("bootstrap complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
