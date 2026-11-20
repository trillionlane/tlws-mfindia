"""Fixed source-only identities; never selected by an HTTP caller."""

from . import contract as c

OWNER = "mfdata_nse_owner"
WRITER = "mfdata_nse_ingest"


def identity(conn, *, owner=False):
    # Only transaction-local configuration; no role switch or authority claim.
    conn.execute("SET LOCAL search_path=pg_catalog")
    rows = conn.execute(
        """SELECT current_database() AS database,current_user AS principal,
        session_user AS session,r.rolname,r.rolsuper,r.rolinherit,r.rolcreatedb,r.rolcreaterole,
        r.rolreplication,r.rolbypassrls,
        EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS membership,
        has_database_privilege(r.oid,current_database(),'CREATE') AS database_create,
        EXISTS(SELECT 1 FROM pg_namespace n WHERE (n.nspname ~ '^mf(_|$)' OR n.nspname='public')
          AND n.nspname<>'mf_nse' AND has_schema_privilege(r.oid,n.oid,'CREATE')) AS canonical_create,
        COALESCE((SELECT has_schema_privilege(r.oid,n.oid,'CREATE') FROM pg_namespace n
                  WHERE n.nspname='mf_nse'),false) AS source_create,
        EXISTS(SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
          WHERE (n.nspname ~ '^mf(_|$)' OR n.nspname='public') AND n.nspname<>'mf_nse'
          AND t.relkind IN ('r','p','v','m')
          AND (t.relowner=r.oid
            OR has_table_privilege(r.oid,t.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
            OR has_any_column_privilege(r.oid,t.oid,'INSERT,UPDATE,REFERENCES')
            OR has_table_privilege(r.oid,t.oid,'SELECT WITH GRANT OPTION'))) AS canonical_write
        FROM pg_roles r WHERE r.rolname=ANY(%s) ORDER BY r.rolname""",
        ([OWNER, WRITER],),
    ).fetchall()
    indexed = {row["rolname"]: row for row in rows}
    c.require(set(indexed) == {OWNER, WRITER}, "NSE_SOURCE_IDENTITY_UNSAFE")
    actor = indexed[OWNER if owner else WRITER]
    c.require(
        actor["database"] == "mfdataindia"
        and actor["principal"] == actor["session"] == (OWNER if owner else WRITER),
        "NSE_SOURCE_IDENTITY_UNSAFE",
    )
    for name, row in indexed.items():
        c.require(
            (name == OWNER or not row["source_create"])
            and not any(
                row[k]
                for k in (
                    "rolsuper",
                    "rolinherit",
                    "rolcreatedb",
                    "rolcreaterole",
                    "rolreplication",
                    "rolbypassrls",
                    "membership",
                    "database_create",
                    "canonical_create",
                    "canonical_write",
                )
            ),
            "NSE_SOURCE_IDENTITY_UNSAFE",
        )
