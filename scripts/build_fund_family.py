"""Build mf.fund_family (own fund identity: tlws_mf_id, slug, tags).

    PYTHONPATH=src python scripts/build_fund_family.py                # populate
    PYTHONPATH=src python scripts/build_fund_family.py --dry-run      # report only

Idempotent upsert on group_key; run after any AMFI load that can add new
families (bootstrap / NAVAll refresh) so new funds get their own ID + slug.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.load.fund_family import build_fund_family  # noqa: E402
from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None,
                    help="target DSN (default: $MFDATAINDIA_DSN); required -- there is "
                         "no built-in default")
    ap.add_argument("--dry-run", action="store_true",
                    help="count the families that would be upserted; write nothing")
    args = ap.parse_args()

    try:
        dsn = resolve_dsn(args.dsn, purpose="the fund-family build")
    except DsnError as exc:
        ap.error(str(exc))
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("fund_family").info("target database: %s", describe_dsn(dsn))
    use_copy = os.environ.get("MF_TEST_NO_COPY") is None
    store = PostgresStore(dsn, use_copy=use_copy)
    with store:
        rep = build_fund_family(store, dry_run=args.dry_run)
        if not args.dry_run:
            store.refresh_dataset_summary("fund_family_build")
    print("FUND_FAMILY REPORT:", rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
