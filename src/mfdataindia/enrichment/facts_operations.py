"""Explicit owner installation with whole-source and prior-layer preservation."""
import hashlib
from pathlib import Path

from psycopg import sql

from mfdataindia.analytics.operations import capture_calendar, compare_calendar
from mfdataindia.analytics.rolling_operations import capture_rolling, compare_rolling
from mfdataindia.enrichment import facts, facts_store
from mfdataindia.enrichment.operations import capture_enrichment
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.preservation import capture_preservation, compare_preservation
from mfdataindia.review.publication_operations import capture_review, compare_review
from mfdataindia.review.privileges import verify_principal
from mfdataindia.store.locks import acquire_nav_data_operation_lock

MIGRATION = "028_official_fund_facts.sql"
SCHEMA_SHA256 = "e2de8323663dd08d420bee3731e7cf2b307798afe51bf4e1ba351cb047229ab4"


def install(conn, root: Path):
    with conn.transaction():
        who = conn.execute("SELECT session_user AS principal,current_user=session_user AS direct").fetchone()
        if not who["direct"] or who["principal"] not in ("postgres", "mfdata_admin"):
            raise ValueError("direct migration owner required")
        if conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"] != "repeatable read":
            raise ValueError("consistent installation required")
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL statement_timeout='120s'")
        conn.execute("SET LOCAL transaction_timeout='180s'")
        acquire_nav_data_operation_lock(conn)
        if conn.execute("SELECT to_regnamespace('mf_official_facts') AS name").fetchone()["name"]:
            for role in facts_store.evidence.ROLES:
                verify_principal(conn, "generator" if role == facts_store.evidence.ROLES[0] else "api", role)
                facts_store.verify_acl(conn, principal=role)
            return {"status": "ALREADY_INSTALLED", "source_rows_changed": 0}
        for namespace in ("mf", "mf_review", "mf_analytics", "mf_rolling", "mf_enrichment"):
            for table in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (namespace,)).fetchall():
                conn.execute(sql.SQL("LOCK TABLE {}.{} IN SHARE MODE").format(sql.Identifier(namespace), sql.Identifier(table["tablename"])))
        before, prior = capture_preservation(conn), capture_review(conn)
        calendar, rolling, enrichment = capture_calendar(conn), capture_rolling(conn), capture_enrichment(conn)
        ddl = (root / "sql/optional" / MIGRATION).read_text()
        if hashlib.sha256(ddl.encode()).hexdigest() != SCHEMA_SHA256:
            raise ValueError("official facts schema differs from reviewed pin")
        if ddl.count("\nBEGIN;\n") != 1 or not ddl.rstrip().endswith("COMMIT;"):
            raise ValueError("unexpected schema transaction shape")
        conn.execute(ddl.replace("\nBEGIN;\n", "\n", 1).rstrip()[:-len("COMMIT;")])
        policy = facts.policy()
        ph = fingerprint(policy)
        review._insert_exact(conn, "policies", "policy_hash", ph, policy,
                             {"policy_id": policy["id"], "policy_version": policy["version"]})
        for role in facts_store.evidence.ROLES:
            verify_principal(conn, "generator" if role == facts_store.evidence.ROLES[0] else "api", role)
            facts_store.verify_acl(conn, principal=role)
        compare_preservation(before, capture_preservation(conn), "")
        compare_review(prior, capture_review(conn), added_policy=ph)
        compare_calendar(calendar, capture_calendar(conn))
        compare_rolling(rolling, capture_rolling(conn))
        if capture_enrichment(conn) != enrichment:
            raise ValueError("prior enrichment changed during installation")
        return {"status": "INSTALLED", "source_rows_changed": 0, "prior_layers_preserved": True, "policy_hash": ph}
