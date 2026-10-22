"""Fixed catalogue-only permission snapshot; never an application read or grant."""
from __future__ import annotations

import hashlib
from pathlib import Path

from mfdataindia.review.policy import fingerprint

IDENTITY = "dev-core-perm-261022a"
STAGE = "core-permission-diagnostic-not-acceptance"
SCHEMAS = ("mf", "mf_live_facts", "mf_review", "public")
RELATIONS = tuple(sorted(
    [("mf", "current_fund_facts", "v"), ("mf", "funds", "r"), ("mf", "fund_variants", "r")]
    + [("mf_live_facts", name, "r" if name == "configuration" else "v") for name in
       ("configuration", "baseline", "field_versions", "field_heads", "selected_fields", "current_values", "resolved")]
    + [("mf_review", name, "r") for name in ("policies", "snapshots", "results", "events", "certifications")]
))
SOURCE_HASHES = {
    "scripts/diagnose_core_fund_plan_dev.py": "7ef6110a5032566b8ab42418134c9bbdd45780bb0978624c336ebac5ae4484ab",
    "src/mfdataindia/api/queries.py": "c2043667ab261be1ae02eb9e5f32658c142e325739487582a555530f0e3f691e",
    "src/mfdataindia/review/privileges.py": "4929964f90292e48f9d81c054e848ed835d190c71a1c54230f82d0e4eb049c33",
    "src/mfdataindia/enrichment/current_facts_store.py": "380f656f5d70a4870c9c86d2f821cc14c418128a1ffb51cf9124c5e3b8b5afc0",
}
CONTEXT_SQL = """SELECT current_user::text AS effective, session_user::text AS authenticated,
       current_database()::text AS database,
       current_setting('transaction_read_only') AS read_only,
       current_setting('transaction_isolation') AS isolation,
       current_setting('statement_timeout') AS statement_timeout,
       current_setting('lock_timeout') AS lock_timeout,
       current_setting('transaction_timeout') AS transaction_timeout,
       pg_catalog.pg_current_snapshot()::text AS snapshot"""
ROLE_SQL = """SELECT r.rolname::text AS role_name, r.rolsuper AS superuser,
       r.rolcreatedb AS create_database, r.rolcreaterole AS create_role,
       r.rolreplication AS replication, r.rolbypassrls AS bypass_rls,
       (SELECT count(*)::int FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid) AS memberships,
       pg_catalog.has_database_privilege(current_database(),'CONNECT') AS can_connect,
       pg_catalog.has_database_privilege(current_database(),'CREATE') AS database_create
FROM pg_catalog.pg_roles r WHERE r.rolname=current_user"""
SCHEMA_SQL = """SELECT target.name AS schema_name, n.oid IS NOT NULL AS object_exists,
       CASE WHEN n.oid IS NOT NULL THEN pg_catalog.has_schema_privilege(n.oid,'USAGE') END AS can_usage,
       CASE WHEN n.oid IS NOT NULL THEN pg_catalog.has_schema_privilege(n.oid,'CREATE') END AS can_create
FROM pg_catalog.unnest(%(schemas)s::text[]) WITH ORDINALITY AS target(name,ordinal)
LEFT JOIN pg_catalog.pg_namespace n ON n.nspname=target.name ORDER BY target.ordinal"""
RELATION_SQL = """SELECT target.schema_name, target.relation_name,
       c.oid IS NOT NULL AS object_exists, c.relkind::text AS relation_kind,
       CASE WHEN c.oid IS NOT NULL THEN pg_catalog.has_table_privilege(c.oid,'SELECT') END AS can_select,
       CASE WHEN c.oid IS NOT NULL THEN pg_catalog.has_any_column_privilege(c.oid,'SELECT') END AS any_column_select,
       CASE WHEN c.oid IS NOT NULL THEN pg_catalog.has_table_privilege(c.oid,'INSERT') END AS can_insert,
       CASE WHEN c.oid IS NOT NULL THEN pg_catalog.has_table_privilege(c.oid,'UPDATE') END AS can_update,
       CASE WHEN c.oid IS NOT NULL THEN pg_catalog.has_table_privilege(c.oid,'DELETE') END AS can_delete,
       CASE WHEN c.oid IS NOT NULL THEN pg_catalog.has_table_privilege(c.oid,'TRUNCATE') END AS can_truncate,
       CASE WHEN c.oid IS NOT NULL THEN pg_catalog.has_any_column_privilege(c.oid,'INSERT,UPDATE') END AS any_column_write,
       c.relrowsecurity AS row_security, c.relforcerowsecurity AS force_row_security,
       CASE WHEN c.oid IS NOT NULL THEN COALESCE('security_invoker=true'=ANY(c.reloptions),false) END AS security_invoker
FROM ROWS FROM (pg_catalog.unnest(%(schemas)s::text[]), pg_catalog.unnest(%(names)s::text[]))
WITH ORDINALITY AS target(schema_name,relation_name,ordinal)
LEFT JOIN pg_catalog.pg_namespace n ON n.nspname=target.schema_name
LEFT JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid AND c.relname=target.relation_name
ORDER BY target.ordinal"""
STATEMENTS = (
    ("role_catalogue", ROLE_SQL, None),
    ("schema_catalogue", SCHEMA_SQL, {"schemas": list(SCHEMAS)}),
    ("relation_catalogue", RELATION_SQL, {"schemas": [s for s, _, _ in RELATIONS],
                                        "names": [n for _, n, _ in RELATIONS]}),
)
BOUNDS = ("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
          "SET LOCAL statement_timeout='5s'", "SET LOCAL lock_timeout='5s'",
          "SET LOCAL transaction_timeout='15s'")
ROLE_FLAGS = ("superuser", "create_database", "create_role", "replication", "bypass_rls", "database_create")
RELATION_FLAGS = ("can_select", "any_column_select", "can_insert", "can_update", "can_delete",
                  "can_truncate", "any_column_write", "row_security", "force_row_security", "security_invoker")
CONTEXT = {"effective": "mfdata_verified_reader", "authenticated": "mfdata_verified_reader",
           "database": "mfdataindia", "read_only": "on", "isolation": "repeatable read",
           "statement_timeout": "5s", "lock_timeout": "5s", "transaction_timeout": "15s"}


def specification():
    return {"schemas": list(SCHEMAS), "relations": [list(r) for r in RELATIONS],
            "statements": [{"stage": label, "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
                            "bindings": bindings} for label, sql, bindings in STATEMENTS],
            "context_sql_sha256": hashlib.sha256(CONTEXT_SQL.encode()).hexdigest(),
            "bounds": list(BOUNDS), "source_hashes": SOURCE_HASHES}


def verify_sources(root):
    for name, expected in SOURCE_HASHES.items():
        if hashlib.sha256((Path(root) / name).read_bytes()).hexdigest() != expected:
            raise ValueError("original failure-attribution source bytes differ")


def validate_context(row):
    if (set(row) != set(CONTEXT) | {"snapshot"}
            or {k: row[k] for k in CONTEXT} != CONTEXT
            or not isinstance(row["snapshot"], str) or not row["snapshot"]):
        raise ValueError("exact direct bounded read-only context required")


def validate_role(row):
    if (set(row) != {"role_name", "memberships", "can_connect", *ROLE_FLAGS}
            or row["role_name"] != "mfdata_verified_reader"
            or type(row["memberships"]) is not int or row["memberships"] != 0
            or row["can_connect"] is not True
            or any(row[k] is not False for k in ROLE_FLAGS)):
        raise ValueError("unexpected elevated or indirect diagnostic principal")


def validate_catalogue(value):
    if set(value) != {"context", "role", "schemas", "relations", "specification_hash"}:
        raise ValueError("complete fixed catalogue snapshot required")
    validate_context(value["context"])
    validate_role(value["role"])
    if value["specification_hash"] != fingerprint(specification()):
        raise ValueError("catalogue specification differs")
    schemas, relations = value["schemas"], value["relations"]
    if (not isinstance(schemas, list) or len(schemas) != len(SCHEMAS)
            or [r.get("schema_name") for r in schemas] != list(SCHEMAS)):
        raise ValueError("all exact ordered schema targets required")
    for row in schemas:
        if set(row) != {"schema_name", "object_exists", "can_usage", "can_create"} or type(row["object_exists"]) is not bool:
            raise ValueError("invalid schema metadata")
        for key in ("can_usage", "can_create"):
            if (row["object_exists"] and type(row[key]) is not bool) or (not row["object_exists"] and row[key] is not None):
                raise ValueError("missing schema is not a privilege denial")
    if (not isinstance(relations, list) or len(relations) != len(RELATIONS)
            or [(r.get("schema_name"), r.get("relation_name")) for r in relations] != [(s, n) for s, n, _ in RELATIONS]):
        raise ValueError("all exact ordered relation targets required")
    for row, (_, _, kind) in zip(relations, RELATIONS, strict=True):
        if (set(row) != {"schema_name", "relation_name", "object_exists", "relation_kind", *RELATION_FLAGS}
                or type(row["object_exists"]) is not bool
                or row["relation_kind"] != (kind if row["object_exists"] else None)):
            raise ValueError("invalid relation metadata/kind")
        for key in RELATION_FLAGS:
            if (row["object_exists"] and type(row[key]) is not bool) or (not row["object_exists"] and row[key] is not None):
                raise ValueError("missing relation is not a privilege denial")
    return value


def validate_result(result):
    wanted = {"level", "catalogue", "catalogue_hash", "worker_binding", "source_hashes",
              "historical_denied_statement_proved", "performance_acceptance", "rollout_acceptance",
              "application_queries_executed", "application_functions_executed", "data_writes",
              "source_requests", "human_events_created", "permission_changes", "role_changes"}
    if (set(result) != wanted or result["level"] != "CURRENT_CATALOGUE_PERMISSIONS_NOT_HISTORICAL_CAUSALITY"
            or result["source_hashes"] != SOURCE_HASHES
            or any(result[k] is not False for k in
                   ("historical_denied_statement_proved", "performance_acceptance", "rollout_acceptance"))
            or any(type(result[k]) is not int or result[k] != 0 for k in
                   ("application_queries_executed", "application_functions_executed", "data_writes",
                    "source_requests", "human_events_created", "permission_changes", "role_changes"))):
        raise ValueError("permission metadata only; no operational or historical acceptance")
    validate_catalogue(result["catalogue"])
    if result["catalogue_hash"] != fingerprint(result["catalogue"]):
        raise ValueError("complete catalogue hash differs")
    return result
