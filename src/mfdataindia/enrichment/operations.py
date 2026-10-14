"""Explicit additive installation; capture prior source and all enhancement layers."""

import hashlib
from pathlib import Path

from psycopg import sql

from mfdataindia.analytics.operations import capture_calendar, compare_calendar
from mfdataindia.analytics.rolling_operations import capture_rolling, compare_rolling
from mfdataindia.enrichment import evidence, store
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.preservation import capture_preservation, compare_preservation
from mfdataindia.review.publication_operations import capture_review, compare_review
from mfdataindia.review.privileges import verify_principal
from mfdataindia.store.locks import acquire_nav_data_operation_lock

MIGRATION = "027_official_enrichment_evidence.sql"
SCHEMA_SHA256 = "52ef41fef91e43c4cc87a98a165f22c96120ad189feaccd7b3d0e551ab15aca8"


def capture_enrichment(conn):
    """Recognize only the exact modelled namespace; stream hashes, never source bytes."""
    if not conn.execute("SELECT to_regnamespace('mf_enrichment') AS namespace").fetchone()[
        "namespace"
    ]:
        return None
    rows = conn.execute(
        "SELECT c.relname,c.relkind,pg_get_userbyid(c.relowner) AS owner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_enrichment' AND c.relkind IN('r','p','v','m','S')"
    ).fetchall()
    if {r["relname"] for r in rows} != {"blobs", "documents", "mappings"} or any(
        r["owner"] not in ("mfdata_admin", "postgres") or r["relkind"] != "r" for r in rows
    ):
        raise ValueError("unmodelled enrichment relation inventory or ownership")
    tables = {}
    for table, key in (
        ("blobs", "content_hash"),
        ("documents", "document_id"),
        ("mappings", "mapping_id"),
    ):
        query = sql.SQL(
            "SELECT {}::text,encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') FROM mf_enrichment.{} t ORDER BY {}"
        ).format(sql.Identifier(key), sql.Identifier(table), sql.Identifier(key))
        digest, count = hashlib.sha256(), 0
        with (
            conn.cursor() as cursor,
            cursor.copy(sql.SQL("COPY ({}) TO STDOUT (FORMAT BINARY)").format(query)) as copy,
        ):
            copy.set_types(["text", "text"])
            for identifier, sealed in copy.rows():
                digest.update((identifier + ":" + sealed + "\n").encode())
                count += 1
        tables[table] = {"rows": count, "sha256": digest.hexdigest()}
    return tables


def install(conn, root: Path):
    with conn.transaction():
        identity = conn.execute(
            "SELECT session_user AS principal,current_user=session_user AS direct"
        ).fetchone()
        if not identity["direct"] or identity["principal"] not in ("postgres", "mfdata_admin"):
            raise ValueError("direct migration owner required")
        if (
            conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            != "repeatable read"
        ):
            raise ValueError("consistent enrichment installation required")
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL statement_timeout='120s'")
        conn.execute("SET LOCAL transaction_timeout='180s'")
        acquire_nav_data_operation_lock(conn)
        if conn.execute("SELECT to_regnamespace('mf_enrichment') AS namespace").fetchone()[
            "namespace"
        ]:
            for role in store.ROLES:
                verify_principal(conn, "generator" if role == store.ROLES[0] else "api", role)
                store.verify_acl(conn, principal=role)
            return {"status": "ALREADY_INSTALLED", "source_rows_changed": 0}
        for namespace in ("mf", "mf_review", "mf_analytics", "mf_rolling"):
            tables = conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename",
                (namespace,),
            ).fetchall()
            for table in tables:
                conn.execute(
                    sql.SQL("LOCK TABLE {}.{} IN SHARE MODE").format(
                        sql.Identifier(namespace), sql.Identifier(table["tablename"])
                    )
                )
        source, prior = capture_preservation(conn), capture_review(conn)
        calendar, rolling = capture_calendar(conn), capture_rolling(conn)
        ddl = (root / "sql/optional" / MIGRATION).read_text()
        if hashlib.sha256(ddl.encode()).hexdigest() != SCHEMA_SHA256:
            raise ValueError("enrichment migration differs from reviewed pin")
        if ddl.count("\nBEGIN;\n") != 1 or not ddl.rstrip().endswith("COMMIT;"):
            raise ValueError("unexpected enrichment transaction shape")
        conn.execute(ddl.replace("\nBEGIN;\n", "\n", 1).rstrip()[: -len("COMMIT;")])
        document = evidence.policy()
        ph = fingerprint(document)
        review._insert_exact(
            conn,
            "policies",
            "policy_hash",
            ph,
            document,
            {"policy_id": document["id"], "policy_version": document["version"]},
        )
        for role in store.ROLES:
            verify_principal(conn, "generator" if role == store.ROLES[0] else "api", role)
            store.verify_acl(conn, principal=role)
        compare_preservation(source, capture_preservation(conn), "")
        compare_review(prior, capture_review(conn), added_policy=ph)
        compare_calendar(calendar, capture_calendar(conn))
        compare_rolling(rolling, capture_rolling(conn))
        return {
            "status": "INSTALLED",
            "source_rows_changed": 0,
            "prior_layers_preserved": True,
            "policy_hash": ph,
        }
