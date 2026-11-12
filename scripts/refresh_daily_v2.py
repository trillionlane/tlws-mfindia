"""Versioned AMFI refresh with stable family IDs and independent price commits."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
import sys

from refresh_daily import (
    IST, integrity_manifest, parse_navall, snapshot_scope, validate_dataset_summary,
    validate_feed, validate_full_snapshot_completeness,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mfdataindia.ingest.amfi_client import AmfiClient  # noqa: E402
from mfdataindia.jobs import nav_continuity as nav  # noqa: E402
from mfdataindia.load import family_continuity as family  # noqa: E402
from mfdataindia.store.dsn import resolve_dsn  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402


def receipt(prices, catalogue, freshness, fetch, *, as_of):
    published = catalogue["state"] in {"CATALOGUE_PUBLISHED", "CATALOGUE_UNCHANGED"}
    return {
        "status": "DAILY_REFRESH_OK" if published else "DAILY_REFRESH_CATALOGUE_HELD",
        "as_of": as_of,
        "fetch": {"content_sha256": fetch.content_sha256},
        "prices": prices,
        "catalogue": catalogue,
        **freshness,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="Read NAV and catalogue freshness")
    mode.add_argument("--install", action="store_true", help="Install the optional family identity schema")
    opts = parser.parse_args(argv)
    dsn = resolve_dsn(None, purpose="the governed AMFI continuity refresh")
    if opts.install:
        root = Path(__file__).resolve().parents[1]
        with PostgresStore(dsn, use_copy=True) as store:
            identity = store.connect().execute(
                "SELECT current_database() AS database, current_user AS principal"
            ).fetchone()
            if identity != {"database": "mfdataindia", "principal": "mfdata_admin"}:
                raise RuntimeError("family identity installation requires the existing owner connection")
            applied = store.apply_migrations(root / "sql/optional", files=("046_fund_family_continuity.sql",))
        print(json.dumps({"applied": applied}, sort_keys=True))
        return 0
    if opts.status:
        # Use a fresh read-only transaction, including with a writer DSN.
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.read_only = True
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            row = conn.execute("""
                SELECT nav_last, dataset_version, (
                    SELECT max(snapshot_date) FROM mf.source_snapshot_runs
                    WHERE source='AMFI' AND snapshot_scope='FULL'
                ) AS catalogue_snapshot_date
                FROM mf.dataset_summary WHERE singleton
            """).fetchone()
        print(json.dumps(row, default=str, sort_keys=True))
        return 0
    scope = snapshot_scope()
    if scope != "FULL":
        parser.error("continuity refresh requires FULL scope")
    as_of = datetime.now(IST).date()
    fetch = AmfiClient(min_delay=0, max_retries=0, timeout=120).fetch_navall()
    schemes, report = parse_navall(fetch.text)
    feed_date = validate_feed(schemes, report, as_of=as_of)
    schemes = family.normalize_issuers(schemes)
    with PostgresStore(dsn) as store:
        # Require the stable identity schema before any data publication.
        store.connect().execute("SELECT 1 FROM mf_identity.variant_families LIMIT 0")
        prices = nav.publish_prices(store, schemes, fetch, as_of=as_of)
        # Keep stdout to one final receipt for the post-success tag hook.
        print(json.dumps({"prices": prices}, default=str, sort_keys=True), file=sys.stderr, flush=True)
        catalogue = nav.publish_catalogue(
            store, schemes, report, fetch, as_of=as_of, feed_date=feed_date, scope=scope,
            integrity_manifest=integrity_manifest,
            validate_completeness=validate_full_snapshot_completeness,
            validate_summary=validate_dataset_summary,
        )
        result = receipt(prices, catalogue, nav.freshness(store), fetch, as_of=as_of)
        print(json.dumps(result, default=str, sort_keys=True), flush=True)
    return 0 if result["status"] == "DAILY_REFRESH_OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
