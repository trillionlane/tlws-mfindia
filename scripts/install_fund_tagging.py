#!/usr/bin/env python3
"""Install or verify the optional factual-tagging sidecar as its direct owner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mfdataindia.tagging import store  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402

SOURCE_MIGRATIONS = (
    "046_fund_family_continuity.sql",
    "047_tag_input_publications.sql",
)


def main() -> int:
    dsn = os.environ.get("MFDATAINDIA_DSN")
    if not dsn:
        print(json.dumps({"reason": "TAGGING_INSTALL_NOT_CONFIGURED", "state": "FAILED"}))
        return 1
    try:
        with PostgresStore(dsn, connect_timeout=5, use_copy=True) as database:
            database.apply_migrations(ROOT / "sql" / "optional", files=SOURCE_MIGRATIONS)
            conn = database.connect()
            with conn.transaction():
                report = store.install(conn, ROOT)
        if report.get("state") not in {"INSTALLED", "ALREADY_INSTALLED", "UPGRADED"}:
            raise ValueError("unexpected install result")
        print(
            json.dumps(
                {
                    "source_rows_changed": report["source_rows_changed"],
                    "state": report["state"],
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception:
        # Never emit a DSN, SQL, source value, or provider exception.
        print(json.dumps({"reason": "TAGGING_INSTALL_FAILED", "state": "FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
