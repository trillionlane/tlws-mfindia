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
from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn
from mfdataindia.store.postgres import PostgresStore


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None,
                    help="target DSN (default: $MFDATAINDIA_DSN); required -- there is "
                         "no built-in default")
    ap.add_argument("--min-delay", type=float, default=0.8)
    ap.add_argument("--max-funds", type=int, default=None)
    ap.add_argument("--all-plans", action="store_true",
                    help="enrich out-of-scope funds too (default: in-scope only)")
    args = ap.parse_args()

    try:
        dsn = resolve_dsn(args.dsn, purpose="Scripbox enrichment")
    except DsnError as exc:
        ap.error(str(exc))
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("enrich_scripbox").info("target database: %s", describe_dsn(dsn))
    use_copy = os.environ.get("MF_TEST_NO_COPY") is None
    store = PostgresStore(dsn, use_copy=use_copy)
    client = ScripboxClient(min_delay=args.min_delay)
    with store:
        rep = enrich_scripbox(store, client,
                              in_scope_only=not args.all_plans,
                              max_funds=args.max_funds)
        print("REPORT:", rep.as_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
