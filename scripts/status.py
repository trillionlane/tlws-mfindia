"""MFDataIndia status: coverage, backfill progress, enrichment progress.

    PYTHONPATH=src python scripts/status.py

Read-only; safe to run any time.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn
from mfdataindia.store.postgres import PostgresStore


def _checkpoint_summary(store, source: str) -> dict:
    rows = store.connect().cursor().execute(
        """
        SELECT entity_kind, status, count(*) AS n,
               coalesce(sum(records_done), 0) AS records
        FROM mf.ingest_checkpoints
        WHERE source = %s
        GROUP BY entity_kind, status ORDER BY entity_kind, status
        """, (source,)).fetchall()
    return rows


def main() -> int:
    try:
        dsn = resolve_dsn(purpose="the status report")
    except DsnError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"target database: {describe_dsn(dsn)}")
    use_copy = os.environ.get("MF_TEST_NO_COPY") is None
    with PostgresStore(dsn, use_copy=use_copy, connect_timeout=30) as store:
        cov = store.coverage()
        span = store.nav_span()
        cur = store.connect().cursor()

        print("=== MFDataIndia status ===")
        print(f"funds            : {cov['schemes_total']} total, {cov['in_scope_live']} live Regular-Plan")
        print(f"AMCs             : {cov['amcs']}   categories: {cov['categories']}")
        print(f"NAV history      : {span['rows']:,} points, {span['schemes']} schemes, "
              f"{span['first_nav_date']} -> {span['last_nav_date']}")

        for label, table in (("fund_facts", "fund_facts"),
                             ("fund_holdings", "fund_holdings"),
                             ("fund_family", "fund_family")):
            n = cur.execute(f"SELECT count(*) AS n FROM mf.{table}").fetchone()["n"]
            print(f"{label:17}: {n:,} rows")

        enr = cur.execute("SELECT * FROM mf.v_enrichment_coverage").fetchone()
        if enr and enr.get("in_scope_live"):
            print(f"enrichment       : {enr['with_facts']}/{enr['in_scope_live']} "
                  f"in-scope funds with facts ({enr['facts_pct']}%)")

        for source in ("AMFI_HISTORY", "AMC", "AMFI"):
            rows = _checkpoint_summary(store, source)
            if rows:
                print(f"\n--- {source} checkpoints ---")
                for r in rows:
                    print(f"  {r['entity_kind']:12} {r['status']:12} {r['n']:6} "
                          f"(records: {r['records']:,})")

        recent = cur.execute(
            """
            SELECT source, entity_kind, count(*) AS n, max(fetched_at) AS latest
            FROM mf.source_metadata GROUP BY source, entity_kind ORDER BY latest DESC
            """).fetchall()
        if recent:
            print("\n--- recent fetches ---")
            for r in recent:
                print(f"  {r['source']:12} {r['entity_kind']:14} x{r['n']:<5} last: {r['latest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
