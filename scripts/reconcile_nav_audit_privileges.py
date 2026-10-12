#!/usr/bin/env python3
"""Idempotently apply the reviewed least-privilege NAV-audit grants.

Migration 020 applies these grants during an ordinary deployment when the
optional audit principal already exists. This bounded operator entry point
also supports the approved late-provisioning order by replaying the same
idempotent SQL after the Cloud SQL user is created.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import psycopg  # noqa: E402

from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn  # noqa: E402


def main() -> int:
    try:
        dsn = resolve_dsn(None, purpose="NAV-audit privilege reconciliation")
    except DsnError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sql_path = Path(__file__).resolve().parent.parent / "sql" / "020_nav_quality_audit_privileges.sql"
    sql = sql_path.read_text(encoding="utf-8")
    with psycopg.connect(dsn, autocommit=True) as conn:
        role = conn.execute("SELECT current_user").fetchone()[0]
        if role != "mfdata_admin":
            raise RuntimeError("privilege reconciliation requires the mfdata_admin DSN")
        principal = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_audit'"
        ).fetchone()
        if principal is None:
            raise RuntimeError("mfdata_audit must be provisioned before privilege reconciliation")
        conn.execute(sql)

    print("NAV_AUDIT_PRIVILEGES_RECONCILED")
    print(f"reconciled target database: {describe_dsn(dsn)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
