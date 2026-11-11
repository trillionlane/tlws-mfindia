"""Bounded, insert-only AMFI history recovery for a reviewed DEV date window."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import sys

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mfdataindia.ingest.amfi_client import AmfiClient, SCHEME_TYPES  # noqa: E402
from mfdataindia.ingest.amfi_nav_history import parse_nav_history_report  # noqa: E402
from mfdataindia.jobs.nav_continuity import missing_history_prices  # noqa: E402
from mfdataindia.load.amfi_to_store import nav_rows  # noqa: E402
from mfdataindia.store.dsn import resolve_dsn  # noqa: E402
from mfdataindia.store.locks import acquire_nav_data_operation_lock  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402


def fetch_window(client, start, end):
    batches = []
    for tp in SCHEME_TYPES:
        fetched = client.fetch_nav_history(start, end, tp)
        rows, report = parse_nav_history_report(fetched.text)
        if (not report.header_rows or report.quarantined or report.unparsed_date
                or report.unparsed_nav or any(row.nav_date is None or not start <= row.nav_date <= end for row in rows)):
            raise RuntimeError(f"invalid AMFI history report for scheme type {tp}")
        batches.append((tp, fetched, list(nav_rows(rows, source="AMFI_HISTORY"))))
    if not any(rows for _, _, rows in batches):
        raise RuntimeError("AMFI history window returned no priced rows")
    return batches


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", type=date.fromisoformat, required=True)
    parser.add_argument("--to-date", type=date.fromisoformat, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-version", type=int)
    parser.add_argument("--expected-rows", type=int)
    parser.add_argument("--source-sha256", nargs=3, metavar="SHA256")
    args = parser.parse_args(argv)
    start, end = args.from_date, args.to_date
    if not 0 <= (end - start).days < 14:
        parser.error("date window must contain 1 to 14 days")
    if args.apply and (not args.expected_version or not args.expected_rows or not args.source_sha256):
        parser.error("--apply requires the reviewed dataset version, positive row count and all three source hashes")
    dsn = resolve_dsn(None, purpose="AMFI NAV gap recovery")
    batches = fetch_window(AmfiClient(min_delay=1, max_retries=0, timeout=120), start, end)
    hashes = [fetch.content_sha256 for _, fetch, _ in batches]
    if args.apply and hashes != args.source_sha256:
        raise RuntimeError("AMFI history content differs from the reviewed inputs")
    prices = [row for _, _, rows in batches for row in rows]

    if args.apply:
        with PostgresStore(dsn) as store, store.transaction() as conn:
            conn.execute("SET LOCAL lock_timeout = '5s'")
            conn.execute("SET LOCAL statement_timeout = '480s'")
            conn.execute("SET LOCAL transaction_timeout = '840s'")
            identity = conn.execute(
                "SELECT current_database() AS database, current_user AS principal"
            ).fetchone()
            if identity != {"database": "mfdataindia", "principal": "mfdata_ingest"}:
                raise RuntimeError("NAV recovery requires the existing MFData ingestion connection")
            acquire_nav_data_operation_lock(conn)
            version = conn.execute("SELECT dataset_version FROM mf.dataset_summary WHERE singleton").fetchone()
            if not version or version["dataset_version"] != args.expected_version:
                raise RuntimeError("dataset version differs from the reviewed baseline")
            rows = missing_history_prices(conn, prices, from_date=start, to_date=end)
            if len(rows) != args.expected_rows:
                raise RuntimeError("missing NAV count differs from the reviewed baseline")
            result = store.upsert_nav(iter(rows), ensure_partitions=False)
            if result.updated or result.inserted != len(rows) or result.quarantined:
                raise RuntimeError("NAV recovery did not insert exactly the reviewed missing keys")
            for tp, fetch, source_rows in batches:
                store.record_fetch(fetch.as_source_metadata(
                    "AMFI_HISTORY", "NAV_HISTORY", f"gap:tp{tp}:{start}:{end}"
                ) | {"records_in": len(source_rows)})
            published = store.refresh_dataset_summary("amfi_nav_gap_recovery")
            if published["nav_last"] < max(row[1] for row in rows):
                raise RuntimeError("published summary did not reflect the recovered NAVs")
        print(json.dumps({"state": "INSERTED_MISSING_NAV", "rows": len(rows),
                          "by_date": dict(sorted(Counter(str(r[1]) for r in rows).items()))}))
    else:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.read_only = True
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            version = conn.execute("SELECT dataset_version FROM mf.dataset_summary WHERE singleton").fetchone()
            rows = missing_history_prices(conn, prices, from_date=start, to_date=end)
        print(json.dumps({
            "state": "REVIEW_MISSING_NAV", "expected_version": version["dataset_version"],
            "source_sha256": hashes, "expected_rows": len(rows),
            "by_date": dict(sorted(Counter(str(r[1]) for r in rows).items())),
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
