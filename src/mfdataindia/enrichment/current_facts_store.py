"""Opt-in current projection. Only the owner can switch its reversible view."""

import hashlib
from pathlib import Path

from psycopg.types.json import Jsonb

from mfdataindia.enrichment import current_facts as engine, store as evidence
from mfdataindia.enrichment import monthly_current, multi_facts, book_sources
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.preservation import capture_preservation, compare_preservation_structure
from mfdataindia.review.publication_operations import capture_review, compare_review
from mfdataindia.store.locks import acquire_nav_data_operation_lock

MIGRATION = "033_current_official_facts.sql"
# Filled from the reviewed migration bytes; drift must fail before DDL.
SCHEMA_SHA256 = "c5cf7b1c04d2ab7a088a19824fb0d951c9b7925bf7fc1846bca28e18fcc10dbb"
VIEWS = {
    "baseline",
    "field_versions",
    "field_heads",
    "selected_fields",
    "current_values",
    "resolved",
}


def known_policy(policy_hash):
    # Old factories do not depend on a newly installed source registry.
    for document in (engine.policy(), monthly_current.policy(), multi_facts.current_policy()):
        if fingerprint(document) == policy_hash:
            return document
    for document in book_sources.current_policies():
        if fingerprint(document) == policy_hash:
            return document
    raise ValueError("unknown current-facts policy")


def configuration(conn):
    rows = conn.execute(
        "SELECT generation,baseline_definition,baseline_sha256,policy_hash,view_hashes,view_definitions FROM mf_live_facts.configuration ORDER BY generation DESC LIMIT 101"
    ).fetchall()
    if not 1 <= len(rows) <= 100 or len({r["baseline_definition"] for r in rows}) != 1:
        raise ValueError(
            "bounded versioned current-facts configuration and one preserved baseline required"
        )
    row = rows[0]
    if (
        row["baseline_sha256"] != hashlib.sha256(row["baseline_definition"].encode()).hexdigest()
        or set(row["view_hashes"]) != VIEWS
        or set(row["view_definitions"]) != VIEWS
        or {k: fingerprint(v) for k, v in row["view_definitions"].items()} != row["view_hashes"]
    ):
        raise ValueError("current-facts configuration/policy differs")
    known_policy(row["policy_hash"])
    return row


def policy_document(conn):
    ph = configuration(conn)["policy_hash"]
    return known_policy(ph)


def view_definition(conn, name):
    return conn.execute(
        "SELECT pg_get_viewdef(%s::regclass,true) AS definition", (name,)
    ).fetchone()["definition"]


def expected_active(conn):
    # Copy the exact query, not a wrapper or whitespace-normalized approximation.
    return view_definition(conn, "mf_live_facts.resolved")


def projection_state(conn):
    original = configuration(conn)["baseline_definition"]
    current = view_definition(conn, "mf.current_fund_facts")
    if current == original:
        return False
    if current != expected_active(conn):
        raise ValueError(
            "current facts projection drift; neither captured baseline nor approved facade"
        )
    return True


def capture(conn):
    if not conn.execute("SELECT to_regnamespace('mf_live_facts') AS namespace").fetchone()[
        "namespace"
    ]:
        return None
    owner = conn.execute(
        "SELECT pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_live_facts'"
    ).fetchone()["owner"]
    objects = conn.execute(
        "SELECT c.relname,c.relkind,pg_get_userbyid(c.relowner) AS owner FROM pg_class c WHERE c.relnamespace='mf_live_facts'::regnamespace AND c.relkind IN('r','p','v','m','S') ORDER BY c.relname"
    ).fetchall()
    if (
        owner not in ("postgres", "mfdata_admin")
        or {r["relname"] for r in objects} != VIEWS | {"configuration"}
        or any(
            r["owner"] != owner or r["relkind"] != ("r" if r["relname"] == "configuration" else "v")
            for r in objects
        )
    ):
        raise ValueError("unmodelled current-facts relation inventory/ownership")
    cfg = configuration(conn)
    if view_definition(conn, "mf_live_facts.baseline") != cfg["baseline_definition"]:
        raise ValueError("captured current-facts baseline drift")
    actual = {
        name: fingerprint(view_definition(conn, "mf_live_facts." + name)) for name in sorted(VIEWS)
    }
    if actual != cfg["view_hashes"]:
        raise ValueError("current-facts view definitions differ from sealed installation")
    history = conn.execute(
        "SELECT to_jsonb(c) AS row FROM mf_live_facts.configuration c ORDER BY generation LIMIT 101"
    ).fetchall()
    return {
        "configuration_hash": fingerprint(dict(cfg)),
        "configuration_history_hash": fingerprint(history),
        "view_hashes": actual,
        "projection_active": projection_state(conn),
    }


def verify_acl(conn):
    capture(conn)
    grants = conn.execute("""WITH acl AS (
        SELECT nspowner AS owner,nspacl AS acl,'schema' AS kind FROM pg_namespace WHERE nspname='mf_live_facts'
        UNION ALL SELECT relowner,relacl,'table' FROM pg_class WHERE relnamespace='mf_live_facts'::regnamespace
        UNION ALL SELECT c.relowner,a.attacl,'column' FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='mf_live_facts'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
    ) SELECT x.grantee,pg_get_userbyid(x.grantee) AS principal,privilege_type,kind FROM acl CROSS JOIN LATERAL aclexplode(acl.acl) x WHERE x.grantee<>owner""").fetchall()
    if any(
        r["principal"] not in evidence.ROLES
        or r["privilege_type"] != ("USAGE" if r["kind"] == "schema" else "SELECT")
        for r in grants
    ):
        raise ValueError("current-facts grants escape read-only role boundary")
    if conn.execute(
        "SELECT 1 FROM pg_proc WHERE pronamespace='mf_live_facts'::regnamespace LIMIT 1"
    ).fetchone():
        raise ValueError("unmodelled current-facts executable function")
    triggers = conn.execute(
        "SELECT tgname,tgenabled FROM pg_trigger WHERE tgrelid='mf_live_facts.configuration'::regclass AND NOT tgisinternal"
    ).fetchall()
    if triggers != [{"tgname": "live_configuration_immutable", "tgenabled": "O"}]:
        raise ValueError("current-facts configuration immutability drift")


def _initialize_configuration(conn, *, baseline, policy_hash):
    """Post-DDL constructor shared with complete migrated installation tests.

    Requires the existing owner's transaction and an empty configuration;
    never upgrades, replaces or activates an installed projection.
    """
    who = conn.execute("SELECT session_user AS name,current_user=session_user AS direct").fetchone()
    if not who["direct"] or who["name"] not in ("postgres", "mfdata_admin") or conn.info.transaction_status.name != "INTRANS":
        raise ValueError("direct transactional configuration owner required")
    if conn.execute("SELECT 1 FROM mf_live_facts.configuration LIMIT 1").fetchone():
        raise ValueError("empty current configuration required")
    definitions = {name: view_definition(conn, "mf_live_facts." + name) for name in sorted(VIEWS)}
    view_hashes = {name: fingerprint(definition) for name, definition in definitions.items()}
    conn.execute("INSERT INTO mf_live_facts.configuration VALUES(1,%s,%s,%s,%s,%s)",
        (baseline, hashlib.sha256(baseline.encode()).hexdigest(), policy_hash,
         Jsonb(view_hashes), Jsonb(definitions)))


def install(conn, root: Path):
    with conn.transaction():
        owner = conn.execute(
            "SELECT session_user AS principal,current_user=session_user AS direct"
        ).fetchone()
        if not owner["direct"] or owner["principal"] not in ("postgres", "mfdata_admin"):
            raise ValueError("direct migration owner required")
        if (
            conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            != "repeatable read"
        ):
            raise ValueError("consistent current-facts installation required")
        acquire_nav_data_operation_lock(conn)
        if conn.execute("SELECT to_regnamespace('mf_live_facts') AS namespace").fetchone()[
            "namespace"
        ]:
            verify_acl(conn)
            return {"status": "ALREADY_INSTALLED", "projection_active": projection_state(conn)}
        # This pinned migration only appends a policy and creates a new schema.
        # Avoid locking/scanning every source table inside its write transaction.
        # Full row preservation is a required separate read-only rollout gate.
        before, prior = capture_preservation(conn, structure_only=True), capture_review(conn)
        baseline = view_definition(conn, "mf.current_fund_facts")
        document = engine.policy()
        ph = fingerprint(document)
        review._insert_exact(
            conn,
            "policies",
            "policy_hash",
            ph,
            document,
            {"policy_id": document["id"], "policy_version": document["version"]},
        )
        ddl = (root / "sql/optional" / MIGRATION).read_text()
        if (
            hashlib.sha256(ddl.encode()).hexdigest() != SCHEMA_SHA256
            or ddl.count("\nBEGIN;\n") != 1
            or not ddl.rstrip().endswith("COMMIT;")
        ):
            raise ValueError("current-facts migration differs from reviewed pin")
        conn.execute(ddl.replace("\nBEGIN;\n", "\n", 1).rstrip()[: -len("COMMIT;")])
        _initialize_configuration(conn, baseline=baseline, policy_hash=ph)
        verify_acl(conn)
        compare_preservation_structure(before, capture_preservation(conn, structure_only=True))
        compare_review(prior, capture_review(conn), added_policy=ph)
        return {
            "status": "INSTALLED",
            "projection_active": False,
            "source_rows_changed": 0,
            "policy_hash": ph,
            "source_structure_preserved": True,
            "whole_source_preservation_requires_post_installation_certification": True,
        }


def switch(conn, *, activate: bool):
    """Owner-only, no data rollback/delete; preserve all other source structures."""
    with conn.transaction():
        who = conn.execute(
            "SELECT session_user AS principal,current_user=session_user AS direct"
        ).fetchone()
        if not who["direct"] or who["principal"] not in ("postgres", "mfdata_admin"):
            raise ValueError("direct projection owner required")
        if (
            conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            != "repeatable read"
        ):
            raise ValueError("consistent projection switch required")
        acquire_nav_data_operation_lock(conn)
        if not activate:
            # Recovery must not depend on candidate view/ACL/source-reference
            # checks which may be the reason activation failed. The owner-only
            # immutable configuration binds the preserved original definition.
            cfg = configuration(conn)
            owner = conn.execute(
                "SELECT pg_get_userbyid(relowner) AS owner FROM pg_class WHERE oid='mf_live_facts.configuration'::regclass"
            ).fetchone()["owner"]
            if owner not in ("postgres", "mfdata_admin"):
                raise ValueError("recovery baseline owner differs")
            conn.execute(
                "CREATE OR REPLACE VIEW mf.current_fund_facts AS " + cfg["baseline_definition"]
            )
            if view_definition(conn, "mf.current_fund_facts") != cfg["baseline_definition"]:
                raise ValueError("baseline view recovery unconfirmed")
            return {
                "projection_active": False,
                "source_rows_changed": 0,
                "core_contract_changed": False,
                "current_view_definition_hash": fingerprint(cfg["baseline_definition"]),
                "whole_source_preservation_requires_post_recovery_certification": True,
            }
        verify_acl(conn)
        # CREATE OR REPLACE holds the hot API view's exclusive lock until commit.
        # Whole-source hashing belongs in a separate read-only inspection, never
        # after this DDL while every reader is waiting for the lock.
        target = expected_active(conn)
        conn.execute("CREATE OR REPLACE VIEW mf.current_fund_facts AS " + target)
        if projection_state(conn) != activate:
            raise ValueError("current-facts projection activation mismatch")
        return {
            "projection_active": activate,
            "source_rows_changed": 0,
            "core_contract_changed": False,
            "current_view_definition_hash": fingerprint(
                view_definition(conn, "mf.current_fund_facts")
            ),
            "whole_source_preservation_requires_post_activation_certification": True,
        }


def get(conn, code):
    from mfdataindia.enrichment.current_graph_read import fields

    identity = evidence.identity(conn, code)
    verify_acl(conn)
    current_policy = policy_document(conn)
    rows = {
        r["field"]: r
        for r in fields(conn, code)
    }
    result = {}
    prior_identity = (
        not rows
        and conn.execute(
            "SELECT 1 FROM mf_official_facts.snapshots WHERE amfi_scheme_code=%s LIMIT 1", (code,)
        ).fetchone()
        is not None
    )
    if not rows and current_policy["version"] >= 2:
        prior_identity = bool(
            conn.execute(
                "SELECT 1 FROM mf_monthly_facts.snapshots s WHERE s.amfi_scheme_code=%s "
                "AND EXISTS(SELECT 1 FROM mf_fact_refresh.publications p WHERE p.snapshot_ids ? s.snapshot_id) "
                "UNION ALL SELECT 1 FROM mf_official_facts.snapshots s WHERE s.amfi_scheme_code=%s "
                "AND EXISTS(SELECT 1 FROM mf_fact_refresh.transitions t WHERE "
                "t.document->>'mode'='activate_monthly_current' AND t.document->'legacy_snapshot_ids' ? s.snapshot_id) LIMIT 1",
                (code, code),
            ).fetchone()
        )
        if current_policy["version"] >= 3:
            prior_identity = prior_identity or bool(
                conn.execute(
                    "SELECT 1 FROM mf_multi_facts.snapshots s WHERE s.amfi_scheme_code=%s AND EXISTS(SELECT 1 FROM mf_multi_facts.runs r WHERE r.status<>'failed' AND r.document->'snapshot_ids' ? s.snapshot_id) LIMIT 1",
                    (code,),
                ).fetchone()
            )
        if current_policy["version"] >= 4:
            prior_identity = prior_identity or bool(
                conn.execute(
                    "SELECT 1 FROM mf_book_facts.batches b CROSS JOIN LATERAL jsonb_array_elements(b.document->'observations') x WHERE (x.value->'identity'->>'code')::integer=%s AND EXISTS(SELECT 1 FROM mf_book_facts.runs r WHERE r.status<>'failed' AND r.document->'batch_ids' ? b.batch_id) LIMIT 1",
                    (code,),
                ).fetchone()
            )
    for field, aliases in current_policy["supported_fields"].items():
        row = rows.get(field)
        conflict = row is not None and row["distinct_values"] != 1
        unusable = row is not None and not row["usable"]
        reason = None
        if conflict or unusable:
            problem = "unusable_disclosure" if unusable else "same_date_conflict"
            reason = problem + (
                "_previous_official_retained" if row["selected_as_of"] else "_baseline_retained"
            )
        elif row is None:
            reason = (
                "official_mapping_identity_changed"
                if prior_identity
                else "no_accepted_official_value"
            )
        elif current_policy["version"] >= 5 and any(
            s.get("status") == "unavailable" for s in row["sources"] or []
        ):
            reason = "incomplete_same_date_evidence_last_usable_retained"
        result[field] = {
            "state": "unusable"
            if unusable
            else "conflicting"
            if conflict
            else "available"
            if row
            else "identity_changed"
            if prior_identity
            else "not_captured",
            "value": row["value"] if row else None,
            "disclosure_as_of": row["selected_as_of"].isoformat()
            if row and row["selected_as_of"]
            else None,
            "latest_disclosure_as_of": row["as_of"].isoformat() if row else None,
            "date_basis": (
                next(iter({s["date_basis"] for s in row["sources"]}))
                if row and row["sources"] and len({s["date_basis"] for s in row["sources"]}) == 1
                else "mixed_source_date_bases"
                if row and row["sources"]
                else "document_context_not_field_effective_date"
            ),
            "verification_status": "needs_verification",
            "freshness_status": "latest_retained_not_current_month_certified",
            "reason": reason,
            "sources": (row["sources"] or [])[:100] if row else [],
            "source_count": len(row["sources"] or []) if row else 0,
            "sources_truncated": len(row["sources"] or []) > 100 if row else False,
            "unresolved_sources": row["latest_sources"][:100] if conflict or unusable else [],
            "unresolved_source_count": len(row["latest_sources"]) if conflict or unusable else 0,
            "unresolved_sources_truncated": len(row["latest_sources"]) > 100
            if conflict or unusable
            else False,
            "core_api_fields": aliases,
        }
    response = {
        "format": "current-official-facts-v1",
        "code": code,
        "policy_hash": fingerprint(current_policy),
        "identity_hash": fingerprint(identity),
        "facts_revision": fingerprint(
            {
                "identity_hash": fingerprint(identity),
                "policy_hash": fingerprint(current_policy),
                "fields": result,
            }
        ),
        "mapping_state": "matched"
        if rows
        else "identity_changed"
        if prior_identity
        else "not_captured",
        "projection_active": projection_state(conn),
        "fields": result,
        "originals_replaced": False,
    }
    from mfdataindia.enrichment import fact_review_store

    if fact_review_store.installed(conn):
        status = fact_review_store.state(conn, code, limit=1)
        for field, value in response["fields"].items():
            value["verification_status"] = status["fields"][field]["status"]
    return response
