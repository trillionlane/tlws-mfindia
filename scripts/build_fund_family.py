"""Build mf.fund_family (own fund identity: tlws_mf_id, slug, tags).

    PYTHONPATH=src python scripts/build_fund_family.py                # populate
    PYTHONPATH=src python scripts/build_fund_family.py --dry-run      # report only

Idempotent upsert on group_key; run after any AMFI load that can add new
families (bootstrap / NAVAll refresh) so new funds get their own ID + slug.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.load import family_continuity  # noqa: E402
from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn  # noqa: E402
from mfdataindia.store.catalogue_lock import catalogue_publication_lock  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402
from mfdataindia.tagging import policy, source_evidence  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


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
        if args.dry_run:
            with store.connect().transaction() as conn:
                counts = conn.execute("""SELECT
                    (SELECT count(DISTINCT group_key) FROM mf.fund_variants) AS variants,
                    (SELECT count(*) FROM mf.fund_family) AS families""").fetchone()
            report = {"dry_run": True, **counts}
        else:
            with catalogue_publication_lock(store.connect(), wait=True):
                with store.transaction() as conn:
                    prior = source_evidence.latest(conn)
                    before = source_evidence.observations(conn)
                    policy.require(
                        before["catalogue_census_hash"] == prior["catalogue_census_hash"],
                        "CATALOGUE_CHANGED_DURING_FAMILY_REBUILD",
                    )
                    family_continuity.rebuild(conn)
                    after = source_evidence.observations(conn)
                    policy.require(
                        after["catalogue_census_hash"] == before["catalogue_census_hash"],
                        "CATALOGUE_CHANGED_DURING_FAMILY_REBUILD",
                    )
                    receipt = source_evidence.append(
                        conn,
                        source_kind="fund-family-rebuild",
                        source_snapshot_run_id=prior["source_snapshot_run_id"],
                        catalogue_observed_on=prior["catalogue_observed_on"],
                        observed_on=datetime.now(IST).date(),
                        expected_catalogue_hash=before["catalogue_census_hash"],
                    )
                    store.refresh_dataset_summary("fund_family_build")
                    report = {
                        "dry_run": False,
                        "catalogue_census_hash": after["catalogue_census_hash"],
                        "family_projection_hash": after["family_projection_hash"],
                        "source_publication_id": receipt["publication_id"],
                        "source_publication_sequence": receipt["publication_sequence"],
                    }
    print("FUND_FAMILY REPORT:", json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
