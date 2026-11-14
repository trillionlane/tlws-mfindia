#!/usr/bin/env python3
"""Install or verify the optional factual-tagging sidecar as its direct owner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mfdataindia.tagging import store  # noqa: E402


def main() -> int:
    dsn = os.environ.get("MFDATAINDIA_DSN")
    if not dsn:
        print(json.dumps({"reason": "TAGGING_INSTALL_NOT_CONFIGURED", "state": "FAILED"}))
        return 1
    try:
        with psycopg.connect(
            dsn,
            autocommit=True,
            connect_timeout=5,
            row_factory=dict_row,
        ) as conn:
            with conn.transaction():
                report = store.install(conn, ROOT)
        if report.get("state") not in {"INSTALLED", "ALREADY_INSTALLED"}:
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
