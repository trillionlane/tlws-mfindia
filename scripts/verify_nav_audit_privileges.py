#!/usr/bin/env python3
"""Fail closed unless the deployed NAV-audit database role is least privilege."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402

EXPECTED_ROLE = "mfdata_audit"
READ_TABLES = {
    "dataset_summary",
    "fund_family",
    "fund_variants",
    "funds",
    "nav_history",
    "nav_quality_assessments",
}
WRITE_TABLE = "nav_quality_assessments"


def main() -> int:
    try:
        dsn = resolve_dsn(None, purpose="NAV-audit privilege verification")
    except DsnError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    with PostgresStore(dsn, use_copy=False, connect_timeout=30) as store:
        conn = store.connect()
        identity = conn.execute(
            """
            SELECT current_user AS role_name,
                   r.rolsuper,
                   r.rolinherit,
                   r.rolcreaterole,
                   r.rolcreatedb,
                   r.rolreplication,
                   r.rolbypassrls,
                   CASE WHEN to_regrole('cloudsqlsuperuser') IS NULL THEN false
                        ELSE pg_has_role(current_user, 'cloudsqlsuperuser', 'member')
                    END AS cloudsqlsuperuser_member,
                   has_database_privilege(current_user, current_database(), 'CREATE')
                       AS database_create,
                   has_schema_privilege(current_user, 'mf', 'CREATE') AS schema_create
              FROM pg_roles r
             WHERE r.rolname = current_user
            """
        ).fetchone()
        if identity is None or identity["role_name"] != EXPECTED_ROLE:
            raise RuntimeError("the audit DSN does not authenticate as mfdata_audit")
        forbidden_attributes = (
            "rolsuper",
            "rolcreaterole",
            "rolcreatedb",
            "rolreplication",
            "rolbypassrls",
            "cloudsqlsuperuser_member",
            "database_create",
            "schema_create",
        )
        if any(identity[name] for name in forbidden_attributes):
            raise RuntimeError("the audit role has an elevated database attribute")
        if identity["rolinherit"]:
            raise RuntimeError("the audit role must not inherit privileges from other roles")

        rows = conn.execute(
            """
            SELECT tablename,
                   has_table_privilege(current_user, format('mf.%I', tablename), 'SELECT')
                       AS can_select,
                   has_table_privilege(current_user, format('mf.%I', tablename), 'INSERT')
                       AS can_insert,
                   has_table_privilege(current_user, format('mf.%I', tablename), 'UPDATE')
                       AS can_update,
                   has_table_privilege(current_user, format('mf.%I', tablename), 'DELETE')
                       AS can_delete,
                   has_table_privilege(current_user, format('mf.%I', tablename), 'TRUNCATE')
                       AS can_truncate
              FROM pg_tables
             WHERE schemaname = 'mf'
             ORDER BY tablename
            """
        ).fetchall()
        observed = {row["tablename"]: row for row in rows}
        if not READ_TABLES.issubset(observed):
            raise RuntimeError("one or more required audit tables are absent")

        for name, row in observed.items():
            if name in READ_TABLES and not row["can_select"]:
                raise RuntimeError(f"the audit role cannot read required table mf.{name}")
            if name not in READ_TABLES and row["can_select"]:
                raise RuntimeError(f"the audit role can read undeclared table mf.{name}")
            write = row["can_insert"] or row["can_update"] or row["can_delete"]
            if name == WRITE_TABLE:
                if not (row["can_insert"] and row["can_update"] and row["can_delete"]):
                    raise RuntimeError("the audit role cannot maintain the assessment table")
            elif write:
                raise RuntimeError(f"the audit role can mutate source table mf.{name}")
            if row["can_truncate"]:
                raise RuntimeError(f"the audit role can truncate mf.{name}")

    print(
        json.dumps(
            {
                "status": "NAV_AUDIT_PRIVILEGES_OK",
                "role": EXPECTED_ROLE,
                "read_tables": sorted(READ_TABLES),
                "write_tables": [WRITE_TABLE],
                "source_table_writes": False,
                "elevated_attributes": False,
            },
            sort_keys=True,
        )
    )
    print(f"verified target database: {describe_dsn(dsn)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
