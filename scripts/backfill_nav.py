"""Run the NAV-history backfill (resumable via mf.ingest_checkpoints).

Assumes mf.funds is already loaded (run bootstrap first). Defaults to the
required 5-year window. Interrupt freely — it resumes.

    PYTHONPATH=src python scripts/backfill_nav.py                 # 5 years
    PYTHONPATH=src python scripts/backfill_nav.py --years 2       # shorter
    PYTHONPATH=src python scripts/backfill_nav.py --force         # reset + full re-run
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
from mfdataindia.jobs.backfill_nav import backfill_nav_history
from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn
from mfdataindia.store.postgres import PostgresStore


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=5.0)
    ap.add_argument("--chunk-days", type=int, default=90)
    ap.add_argument("--min-delay", type=float, default=1.0)
    ap.add_argument("--dsn", default=None,
                    help="target DSN (default: $MFDATAINDIA_DSN); required -- there is "
                         "no built-in default")
    ap.add_argument("--force", action="store_true",
                    help="reset AMFI_HISTORY checkpoints and re-backfill from scratch")
    args = ap.parse_args()

    try:
        dsn = resolve_dsn(args.dsn, purpose="the AMFI NAV backfill")
    except DsnError as exc:
        ap.error(str(exc))
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("backfill_nav").info("target database: %s", describe_dsn(dsn))
    use_copy = os.environ.get("MF_TEST_NO_COPY") is None
    store = PostgresStore(dsn, use_copy=use_copy)
    client = AmfiClient(min_delay=args.min_delay)

    today = date.today()
    from_date = today - timedelta(days=int(args.years * 365.25))

    with store:
        if args.force:
            with store.transaction() as conn, conn.cursor() as cur:
                n = cur.execute(
                    "DELETE FROM mf.ingest_checkpoints WHERE source='AMFI_HISTORY'").rowcount
            logging.info("--force: reset %d backfill checkpoints", n)
        rep = backfill_nav_history(
            store, client, from_date, today, chunk_days=args.chunk_days, strict=False)
        print("BACKFILL REPORT:", rep.as_dict())
        print("nav_span:", store.nav_span())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
