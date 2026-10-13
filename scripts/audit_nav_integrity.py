#!/usr/bin/env python3
"""Assess NAV-integrity detection signals across the complete database.

Runs the governed NAV-integrity audit (mfdataindia.audit.nav_integrity) over
the FULL NAV history — not a sample, not a known-code list — and refreshes
``mf.nav_quality_assessments``. Deterministic and idempotent: the same
database yields the same signals and the same assessment rows on every run.

Safety:

* read-only over ``mf.nav_history`` — the audit never mutates stored NAVs;
* its only write target is ``mf.nav_quality_assessments``;
* ``--dry-run`` reports without writing anything;
* the write is one transaction (all-or-nothing).

Usage:

    PYTHONPATH=src python scripts/audit_nav_integrity.py --dry-run
    PYTHONPATH=src python scripts/audit_nav_integrity.py \
        --expected-dataset-version 7
    PYTHONPATH=src python scripts/audit_nav_integrity.py \
        --expected-dataset-version 7 --certify-current

The JSON summary (methodology version, dataset version, counts, affected
codes or a bounded findings hash) goes to stdout; progress goes to stderr.
The DEV schedule remains a separately approved activation gate.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.audit import nav_integrity  # noqa: E402
from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn  # noqa: E402
from mfdataindia.store.locks import acquire_nav_data_operation_lock  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402

log = logging.getLogger("audit_nav_integrity")


def source_manifest(conn) -> dict[str, object]:
    """Small immutable-source manifest used to prove the audit changed no source."""

    row = conn.execute(
        """
        SELECT ds.dataset_version,
               ds.source_content_hash,
               ds.nav_first,
               ds.nav_last,
               ds.nav_rows,
               (SELECT count(*) FROM mf.funds) AS funds,
               (SELECT count(*) FROM mf.fund_variants) AS fund_variants,
               (SELECT count(*) FROM mf.fund_family) AS fund_families
          FROM mf.dataset_summary ds
         WHERE ds.singleton
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("dataset summary is unavailable")
    return {
        "dataset_version": int(row["dataset_version"]),
        "source_content_hash": row["source_content_hash"],
        "nav_first": row["nav_first"].isoformat() if row["nav_first"] else None,
        "nav_last": row["nav_last"].isoformat() if row["nav_last"] else None,
        "nav_rows": int(row["nav_rows"]),
        "funds": int(row["funds"]),
        "fund_variants": int(row["fund_variants"]),
        "fund_families": int(row["fund_families"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="database DSN (default: $MFDATAINDIA_DSN)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="assess and report without writing mf.nav_quality_assessments",
    )
    parser.add_argument(
        "--expected-dataset-version",
        type=int,
        help="fail before writes unless the stable snapshot has this exact version",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="omit scheme-code lists while retaining counts and a deterministic findings hash",
    )
    parser.add_argument(
        "--certify-current",
        action="store_true",
        help="read-only exact comparison of persisted assessments with a fresh full scan",
    )
    parser.add_argument(
        "--report-dataset-version",
        action="store_true",
        help="read-only lock-protected report of the current dataset version",
    )
    args = parser.parse_args()
    selected_modes = sum((args.dry_run, args.certify_current, args.report_dataset_version))
    if selected_modes > 1:
        parser.error(
            "--dry-run, --certify-current and --report-dataset-version are mutually exclusive"
        )

    try:
        dsn = resolve_dsn(args.dsn, purpose="the NAV-integrity audit")
    except DsnError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log.info("target database: %s", describe_dsn(dsn))
    log.info(
        "scanning the complete NAV history; this is a bounded full-history "
        "audit, not a request-path query"
    )

    started = perf_counter()
    with PostgresStore(dsn, use_copy=False, connect_timeout=30) as store:
        with store.transaction() as conn:
            # Keep the full-history scan, dataset version, and persisted
            # assessments on one stable snapshot if a daily refresh overlaps
            # this separately scheduled operator run. A later refresh then
            # advances dataset_version and makes these assessments stale.
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            conn.execute("SET LOCAL statement_timeout = '120s'")
            acquire_nav_data_operation_lock(conn)
            before = source_manifest(conn)
            if args.report_dataset_version:
                observed_version = int(before["dataset_version"])
                if observed_version <= 0:
                    raise RuntimeError("the dataset has no positive version")
                if (
                    args.expected_dataset_version is not None
                    and observed_version != args.expected_dataset_version
                ):
                    raise RuntimeError(
                        "dataset version changed before activation: "
                        f"expected={args.expected_dataset_version} actual={observed_version}"
                    )
                summary = {
                    "status": "NAV_DATASET_VERSION_CURRENT",
                    "dataset_version": observed_version,
                    "assessments_written": 0,
                }
            else:
                summary = nav_integrity.assess(
                    conn,
                    write=not (args.dry_run or args.certify_current),
                    expected_dataset_version=args.expected_dataset_version,
                    skip_if_current=not (args.dry_run or args.certify_current),
                    verify_current=args.certify_current,
                )
            after = source_manifest(conn)
            if before != after:
                raise RuntimeError("audit changed an authoritative source-data manifest")
            summary["source_manifest"] = after
    summary["duration_ms"] = int((perf_counter() - started) * 1000)
    if args.summary_only and "findings_sha256" in summary:
        summary = nav_integrity.summary_without_codes(summary)
    print(json.dumps(summary, sort_keys=True, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
