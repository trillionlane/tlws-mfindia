#!/usr/bin/env python3
"""Publish version-bound NAV metrics and profiles, without modifying source facts.

Each fund is committed separately under the shared source lock. Stale or
unversioned calculations remain unavailable through the API until published.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.jobs.nav_calculations import recompute  # noqa: E402
from mfdataindia.store.dsn import resolve_dsn  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn")
    parser.add_argument("--min-points", type=int, default=252, choices=[252],
                        help="profile methodology uses a fixed 252-NAV minimum")
    args = parser.parse_args()
    with PostgresStore(resolve_dsn(args.dsn, purpose="versioned metrics"), use_copy=False) as store:
        with store.transaction() as conn:
            codes = [r["amfi_scheme_code"] for r in conn.execute(
                "SELECT DISTINCT amfi_scheme_code FROM mf.nav_history ORDER BY amfi_scheme_code"
            ).fetchall()]
        for code in codes:
            with store.transaction() as conn:
                conn.execute("SET LOCAL statement_timeout = '120s'")
                recompute(conn, [code])
    print(json.dumps({"status": "VERSIONED_METRICS_PUBLISHED", "funds": len(codes), "source_writes": 0}))


if __name__ == "__main__":
    main()
