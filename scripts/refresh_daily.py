#!/usr/bin/env python3
"""Run one bounded, authoritative AMFI NAVAll refresh.

The job makes exactly one upstream request and relies on Cloud Run Job
``maxRetries=0``. It updates scheme metadata and variants, writes NAV only for
in-scope schemes, refreshes fund-family identity, and fails closed on feed or
post-write integrity drift.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import fields
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.ingest.amfi_client import AmfiClient  # noqa: E402
from mfdataindia.ingest.amfi_navall import AmfiScheme, ParseReport, parse_navall  # noqa: E402
from mfdataindia.load.amfi_to_store import load_parsed_amfi, nav_rows  # noqa: E402
from mfdataindia.load.fund_family import build_fund_family  # noqa: E402
from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402

log = logging.getLogger("daily_refresh")
IST = ZoneInfo("Asia/Kolkata")
SOURCE_ENTITY_KIND = "LATEST_NAV"


def validate_feed(schemes: Sequence[AmfiScheme], report: ParseReport, *, as_of: date) -> date:
    if report.format not in {"NAVALL_8COL", "NAVALL_6COL"}:
        raise RuntimeError(f"unexpected NAVAll format: {report.format}")
    if report.data_rows < 1_000 or len(schemes) < 1_000 or report.distinct_amcs < 20:
        raise RuntimeError(
            "NAVAll feed is unexpectedly small: "
            f"rows={report.data_rows} schemes={len(schemes)} amcs={report.distinct_amcs}"
        )
    quarantine_limit = max(25, report.data_rows // 100)
    if report.quarantined > quarantine_limit:
        raise RuntimeError(
            f"NAVAll quarantine count {report.quarantined} exceeds {quarantine_limit}"
        )
    nav_dates = [scheme.nav_date for scheme in schemes if scheme.nav_date is not None]
    if not nav_dates:
        raise RuntimeError("NAVAll feed contains no parsed NAV dates")
    feed_date = max(nav_dates)
    age_days = (as_of - feed_date).days
    if age_days < 0 or age_days > 7:
        raise RuntimeError(
            f"NAVAll feed date {feed_date.isoformat()} is not within 0..7 days of {as_of}"
        )
    return feed_date


def integrity_manifest(store: PostgresStore) -> dict[str, Any]:
    with store.connect().cursor() as cur:
        return cur.execute(
            """
            SELECT
                (SELECT count(*) FROM mf.funds) AS funds,
                (SELECT count(*) FROM mf.funds WHERE in_scope AND NOT is_defunct) AS in_scope_live,
                (SELECT count(*) FROM mf.nav_history) AS nav_rows,
                (SELECT min(nav_date) FROM mf.nav_history) AS nav_min_date,
                (SELECT max(nav_date) FROM mf.nav_history) AS nav_max_date,
                (SELECT count(*) FROM mf.fund_family) AS fund_families,
                (SELECT count(*) FROM (
                    SELECT DISTINCT group_key FROM mf.fund_variants
                ) v WHERE NOT EXISTS (
                    SELECT 1 FROM mf.fund_family f WHERE f.group_key = v.group_key
                )) AS missing_families,
                (SELECT count(*) FROM mf.fund_family f WHERE NOT EXISTS (
                    SELECT 1 FROM mf.fund_variants v WHERE v.group_key = f.group_key
                )) AS orphan_families,
                (SELECT count(*) FROM mf.nav_history_default) AS default_partition_rows
            """
        ).fetchone()


def parse_report_payload(report: ParseReport) -> dict[str, Any]:
    """Return a JSON-safe representation of a NAVAll parse report.

    ``dataclasses.asdict`` reconstructs ``Counter`` values from an iterable of
    ``(key, value)`` pairs. ``Counter`` interprets those pairs as keys, leaving
    tuple keys that the JSON encoder rejects. Read the slotted dataclass fields
    directly so the original string-keyed counters remain JSON mappings.
    """
    return {field.name: getattr(report, field.name) for field in fields(report)}


def main() -> int:
    try:
        dsn = resolve_dsn(None, purpose="the governed daily AMFI refresh")
    except DsnError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("target database: %s", describe_dsn(dsn))
    as_of = (
        date.fromisoformat(os.environ["REFRESH_AS_OF_DATE"])
        if os.environ.get("REFRESH_AS_OF_DATE")
        else datetime.now(IST).date()
    )

    client = AmfiClient(min_delay=0.0, max_retries=0, timeout=120.0)
    fetched = client.fetch_navall()
    schemes, parse_report = parse_navall(fetched.text)
    feed_date = validate_feed(schemes, parse_report, as_of=as_of)
    in_scope_schemes = [scheme for scheme in schemes if scheme.in_scope]
    if not in_scope_schemes:
        raise RuntimeError("NAVAll feed contains no in-scope schemes")

    store = PostgresStore(dsn)
    with store:
        before = integrity_manifest(store)
        load_report = load_parsed_amfi(
            store,
            schemes,
            source_date=feed_date,
            include_nav=False,
        )
        load_report["nav"] = store.upsert_nav(nav_rows(in_scope_schemes)).as_dict()
        family_report = build_fund_family(store)
        store.record_fetch(
            fetched.as_source_metadata("AMFI", SOURCE_ENTITY_KIND, feed_date.isoformat())
            | {
                "records_in": len(schemes),
                "records_ok": len(schemes),
                "records_quarantined": parse_report.quarantined,
                "duration_ms": fetched.duration_ms,
            }
        )
        after = integrity_manifest(store)

    if int(after["funds"]) < int(before["funds"]):
        raise RuntimeError("fund count decreased during refresh")
    if int(after["nav_rows"]) < int(before["nav_rows"]):
        raise RuntimeError("NAV row count decreased during refresh")
    if after["nav_max_date"] < before["nav_max_date"]:
        raise RuntimeError("maximum NAV date regressed during refresh")
    if after["nav_max_date"] < feed_date:
        raise RuntimeError("loaded NAV maximum is older than the fetched feed")
    if any(
        int(after[key]) != 0
        for key in ("missing_families", "orphan_families", "default_partition_rows")
    ):
        raise RuntimeError(f"post-refresh integrity checks failed: {after!r}")

    output = {
        "status": "DAILY_REFRESH_OK",
        "as_of": as_of,
        "feed_date": feed_date,
        "fetch": {
            "http_status": fetched.http_status,
            "content_sha256": fetched.content_sha256,
            "content_bytes": fetched.content_bytes,
            "duration_ms": fetched.duration_ms,
        },
        "parse": parse_report_payload(parse_report),
        "load": load_report,
        "fund_family": family_report,
        "before": before,
        "after": after,
    }
    print(json.dumps(output, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
