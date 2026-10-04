"""Run the Groww enrichment backfill (resumable via mf.ingest_checkpoints).

Run this after the Scripbox crawl completes. Default mode fills only the gaps
(funds Scripbox missed, or those lacking a benchmark). Use --all to enrich every
live in-scope fund with Groww-only fields (benchmark, fund-manager bios,
expense-ratio history, sector-tagged holdings).

    PYTHONPATH=src python scripts/enrich_groww.py            # gaps only
    PYTHONPATH=src python scripts/enrich_groww.py --all      # every fund
    PYTHONPATH=src python scripts/enrich_groww.py --max-funds 50
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.ingest.groww_client import GrowwClient
from mfdataindia.jobs.enrich_groww import enrich_groww
from mfdataindia.store.postgres import PostgresStore

DEFAULT_DSN = "host=127.0.0.1 port=5433 user=postgres dbname=postgres sslmode=disable"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("MFDATAINDIA_DSN", DEFAULT_DSN))
    ap.add_argument("--min-delay", type=float, default=0.6)
    ap.add_argument("--max-funds", type=int, default=None)
    ap.add_argument("--all", action="store_true", help="enrich all in-scope funds, not just gaps")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    use_copy = os.environ.get("MF_TEST_NO_COPY") is None
    store = PostgresStore(args.dsn, use_copy=use_copy)
    client = GrowwClient(min_delay=args.min_delay)
    with store:
        rep = enrich_groww(store, client, mode="all" if args.all else "gaps",
                           max_funds=args.max_funds)
        print("REPORT:", rep.as_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
