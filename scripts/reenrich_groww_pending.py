"""Fill the three pending Groww fields on live in-scope funds.

Targets ``registrar_agent``, ``base_expense_ratio`` and ``expense_ratio_history``
for funds that ``mf.v_fund_data_status`` reports as still missing them. Resumable
via its own ``GROWW/ENRICH_PENDING`` checkpoint kind, so it is not blocked by the
4,271 funds already marked DONE from the original enrichment pass.

The database must be stated explicitly -- there is no default, because a stale
PGlite instance is still listening on 5433 and a run against it succeeds silently
while the live data is untouched.

    export MFDATAINDIA_DSN='postgresql://postgres:secret@localhost:5432/mfdataindia'

    PYTHONPATH=src python scripts/reenrich_groww_pending.py --dry-run   # count only
    PYTHONPATH=src python scripts/reenrich_groww_pending.py --max-funds 5   # trial
    PYTHONPATH=src python scripts/reenrich_groww_pending.py             # full pass

Writes are non-destructive: Scripbox-owned fund_facts columns are gap-filled only
and existing holdings snapshots are never replaced.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.ingest.groww_client import GrowwClient
from mfdataindia.jobs.reenrich_groww_pending import reenrich_pending
from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn
from mfdataindia.store.postgres import PostgresStore


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", default=None,
                    help=f"target DSN (default: ${os.environ.get('MFDATAINDIA_DSN', 'MFDATAINDIA_DSN')})")
    ap.add_argument("--min-delay", type=float, default=1.0,
                    help="seconds between Groww requests (default 1.0)")
    ap.add_argument("--max-funds", type=int, default=None,
                    help="stop after this many fetches; use for a trial batch")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve and count targets, then exit without fetching")
    ap.add_argument("--strict", action="store_true", help="abort on first failure")
    args = ap.parse_args()

    try:
        dsn = resolve_dsn(args.dsn, purpose="Groww pending re-enrichment")
    except DsnError as exc:
        ap.error(str(exc))
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("reenrich")
    log.info("target database: %s", describe_dsn(dsn))

    use_copy = os.environ.get("MF_TEST_NO_COPY") is None
    store = PostgresStore(dsn, use_copy=use_copy)
    client = GrowwClient(min_delay=args.min_delay)
    with store:
        rep = reenrich_pending(store, client, max_funds=args.max_funds,
                               dry_run=args.dry_run, strict=args.strict)
        print("REPORT:", rep.as_dict())
        if rep.failures:
            print("FIRST FAILURES:")
            for line in rep.failures[:10]:
                print("  ", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
