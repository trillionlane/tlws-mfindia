"""Run the Scripbox enrichment crawl (resumable via mf.ingest_checkpoints).

    PYTHONPATH=src python scripts/enrich_scripbox.py
    PYTHONPATH=src python scripts/enrich_scripbox.py --max-funds 50   # test

Re-running resumes from checkpoints; nothing already enriched is re-fetched.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.ingest.scripbox_client import ScripboxClient
from mfdataindia.jobs.enrich_scripbox import enrich_scripbox
from mfdataindia.store.postgres import PostgresStore

DEFAULT_DSN = "host=127.0.0.1 port=5433 user=postgres dbname=postgres sslmode=disable"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("MFDATAINDIA_DSN", DEFAULT_DSN))
    ap.add_argument("--min-delay", type=float, default=0.8)
    ap.add_argument("--max-funds", type=int, default=None)
    ap.add_argument("--all-plans", action="store_true",
                    help="enrich out-of-scope funds too (default: in-scope only)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    use_copy = os.environ.get("MF_TEST_NO_COPY") is None
    store = PostgresStore(args.dsn, use_copy=use_copy)
    client = ScripboxClient(min_delay=args.min_delay)
    with store:
        rep = enrich_scripbox(store, client,
                              in_scope_only=not args.all_plans,
                              max_funds=args.max_funds)
        print("REPORT:", rep.as_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
