"""Owner-only opt-in installation with every prior layer preserved."""

import hashlib
from pathlib import Path
from psycopg import sql
from mfdataindia.analytics.operations import capture_calendar, compare_calendar
from mfdataindia.analytics.rolling_operations import capture_rolling, compare_rolling
from mfdataindia.enrichment import portfolios, portfolio_store, facts_store
from mfdataindia.enrichment.operations import capture_enrichment
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.preservation import capture_preservation, compare_preservation
from mfdataindia.review.publication_operations import capture_review, compare_review
from mfdataindia.review.privileges import verify_principal
from mfdataindia.store.locks import acquire_nav_data_operation_lock

MIGRATION = "029_official_complete_portfolios.sql"
SCHEMA_SHA256 = "59ea0a46e7cd86cfc61c22667c30428d9a9cd0020346d3fb91f4ea54e83f453e"


def install(conn, root: Path):
    with conn.transaction():
        who = conn.execute(
            "SELECT session_user AS principal,current_user=session_user AS direct"
        ).fetchone()
        if (
            not who["direct"]
            or who["principal"] not in ("postgres", "mfdata_admin")
            or conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            != "repeatable read"
        ):
            raise ValueError("direct consistent migration owner required")
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL statement_timeout='120s'")
        acquire_nav_data_operation_lock(conn)
        if conn.execute("SELECT to_regnamespace('mf_portfolios') AS name").fetchone()["name"]:
            portfolio_store.verify_acl(conn, principal=portfolio_store.evidence.ROLES[1])
            return {"status": "ALREADY_INSTALLED", "source_rows_changed": 0}
        for ns in (
            "mf",
            "mf_review",
            "mf_analytics",
            "mf_rolling",
            "mf_enrichment",
            "mf_official_facts",
        ):
            for table in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (ns,)
            ).fetchall():
                conn.execute(
                    sql.SQL("LOCK TABLE {}.{} IN SHARE MODE").format(
                        sql.Identifier(ns), sql.Identifier(table["tablename"])
                    )
                )
        before, prior = capture_preservation(conn), capture_review(conn)
        calendar, rolling = capture_calendar(conn), capture_rolling(conn)
        enrichment, facts = capture_enrichment(conn, include_rows=True), facts_store.capture(conn)
        ddl = (root / "sql/optional" / MIGRATION).read_text()
        if (
            hashlib.sha256(ddl.encode()).hexdigest() != SCHEMA_SHA256
            or ddl.count("\nBEGIN;\n") != 1
            or not ddl.rstrip().endswith("COMMIT;")
        ):
            raise ValueError("portfolio schema differs from reviewed pin")
        conn.execute(ddl.replace("\nBEGIN;\n", "\n", 1).rstrip()[: -len("COMMIT;")])
        policy = portfolios.policy()
        ph = fingerprint(policy)
        review._insert_exact(
            conn,
            "policies",
            "policy_hash",
            ph,
            policy,
            {"policy_id": policy["id"], "policy_version": policy["version"]},
        )
        for role in portfolio_store.evidence.ROLES:
            verify_principal(
                conn, "generator" if role == portfolio_store.evidence.ROLES[0] else "api", role
            )
            portfolio_store.verify_acl(conn, principal=role)
        compare_preservation(before, capture_preservation(conn), "")
        compare_review(prior, capture_review(conn), added_policy=ph)
        compare_calendar(calendar, capture_calendar(conn))
        compare_rolling(rolling, capture_rolling(conn))
        if (
            capture_enrichment(conn, include_rows=True) != enrichment
            or facts_store.capture(conn) != facts
        ):
            raise ValueError("prior official enrichment changed")
        return {
            "status": "INSTALLED",
            "source_rows_changed": 0,
            "prior_layers_preserved": True,
            "policy_hash": ph,
        }
