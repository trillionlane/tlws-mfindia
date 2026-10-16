"""Immutable monthly originals/reporting snapshots with effective privilege checks."""

import hashlib
from pathlib import Path

from psycopg.types.json import Jsonb

from mfdataindia.enrichment import (
    facts,
    monthly_facts as engine,
    monthly_pdf,
    monthly_schemas as schemas,
)
from mfdataindia.enrichment import store as evidence
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_principal, verify_runtime
from mfdataindia.store.locks import acquire_nav_data_operation_lock

MIGRATION = "034_official_monthly_facts.sql"
SCHEMA_SHA256 = "1363ec9ddddbb331e0f822dc3262777b0725cf48da8ebcc61adc46154d60e631"


def verify_acl(conn, *, principal=None):
    principal = principal or conn.execute("SELECT current_user AS name").fetchone()["name"]
    if principal not in evidence.ROLES:
        raise ValueError("dedicated monthly-reporting principal required")
    verify_principal(conn, "generator" if principal == evidence.ROLES[0] else "api", principal)
    row = conn.execute(
        "SELECT nspowner,pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_monthly_facts'"
    ).fetchone()
    if not row or row["owner"] not in ("postgres", "mfdata_admin"):
        raise ValueError("monthly namespace ownership differs")
    objects = conn.execute(
        "SELECT relname,relkind,relowner FROM pg_class WHERE relnamespace='mf_monthly_facts'::regnamespace AND relkind IN('r','p','v','m','S')"
    ).fetchall()
    if {r["relname"] for r in objects} != {"sources", "snapshots"} or any(
        r["relkind"] != "r" or r["relowner"] != row["nspowner"] for r in objects
    ):
        raise ValueError("monthly relation inventory differs")
    functions = conn.execute(
        "SELECT proname,prosecdef,proowner,proconfig FROM pg_proc WHERE pronamespace='mf_monthly_facts'::regnamespace"
    ).fetchall()
    if functions != [
        {
            "proname": "seal_snapshot",
            "prosecdef": True,
            "proowner": row["nspowner"],
            "proconfig": ["search_path=pg_catalog"],
        }
    ]:
        raise ValueError("monthly function inventory or owner boundary differs")
    for table, triggers in (
        ("sources", {"monthly_source_immutable"}),
        ("snapshots", {"monthly_snapshot_sealed", "monthly_snapshot_immutable"}),
    ):
        actual = conn.execute(
            "SELECT tgname,tgenabled FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal",
            ("mf_monthly_facts." + table,),
        ).fetchall()
        if {r["tgname"] for r in actual} != triggers or any(r["tgenabled"] != "O" for r in actual):
            raise ValueError("monthly immutable/sealed triggers differ")
        state = conn.execute(
            "SELECT has_table_privilege(%s,%s,'INSERT') OR has_any_column_privilege(%s,%s,'INSERT') AS append,has_table_privilege(%s,%s,'UPDATE,DELETE,TRUNCATE') OR has_any_column_privilege(%s,%s,'UPDATE') AS rewrite",
            (principal, "mf_monthly_facts." + table) * 4,
        ).fetchone()
        if state != {"append": principal == evidence.ROLES[0], "rewrite": False}:
            raise ValueError("monthly effective DML capabilities differ")
    raw = conn.execute(
        "SELECT has_column_privilege(%s,'mf_monthly_facts.sources','content','SELECT') AS allowed",
        (principal,),
    ).fetchone()["allowed"]
    if raw != (principal == evidence.ROLES[0]):
        raise ValueError("monthly original-byte capability differs")
    if conn.execute(
        "SELECT has_schema_privilege(%s,'mf_monthly_facts','CREATE') AS allowed", (principal,)
    ).fetchone()["allowed"]:
        raise ValueError("runtime monthly DDL not allowed")
    grants = conn.execute("""WITH objects AS (
        SELECT relowner AS owner,relacl AS acl FROM pg_class WHERE relnamespace='mf_monthly_facts'::regnamespace
        UNION ALL SELECT c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='mf_monthly_facts'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
        UNION ALL SELECT nspowner,nspacl FROM pg_namespace WHERE nspname='mf_monthly_facts'
        UNION ALL SELECT proowner,COALESCE(proacl,acldefault('f'::\"char\",proowner)) FROM pg_proc WHERE pronamespace='mf_monthly_facts'::regnamespace
    ) SELECT pg_get_userbyid(x.grantee) AS principal,x.privilege_type FROM objects CROSS JOIN LATERAL aclexplode(acl) x WHERE x.grantee<>owner""").fetchall()
    if any(
        r["principal"] not in evidence.ROLES
        or r["privilege_type"]
        not in (
            {"SELECT", "USAGE", "INSERT"}
            if r["principal"] == evidence.ROLES[0]
            else {"SELECT", "USAGE"}
        )
        for r in grants
    ):
        raise ValueError("monthly grants escape the bounded roles")
    document = conn.execute(
        "SELECT document FROM mf_review.policies WHERE policy_hash=%s",
        (fingerprint(engine.policy()),),
    ).fetchone()
    if not document or document["document"] != engine.policy():
        raise ValueError("monthly reporting policy missing or differs")


def capture(conn):
    if not conn.execute("SELECT to_regnamespace('mf_monthly_facts') AS name").fetchone()["name"]:
        return None
    verify_acl(conn, principal=evidence.ROLES[1])
    result = {
        "function_hash": fingerprint(
            conn.execute(
                "SELECT pg_get_functiondef(p.oid) AS definition FROM pg_proc p WHERE pronamespace='mf_monthly_facts'::regnamespace ORDER BY proname"
            ).fetchall()
        )
    }
    for table, key in (("sources", "content_hash"), ("snapshots", "snapshot_id")):
        relation = (
            "(SELECT content_hash,byte_length,intake FROM mf_monthly_facts.sources)"
            if table == "sources"
            else "mf_monthly_facts.snapshots"
        )
        rows = conn.execute(
            "SELECT "
            + key
            + ",encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS sha256 FROM "
            + relation
            + " t ORDER BY "
            + key
        ).fetchall()
        result[table] = {
            "rows": len(rows),
            "sha256": fingerprint(rows),
            "row_hashes": {r[key]: r["sha256"] for r in rows},
        }
    raw_allowed = conn.execute(
        "SELECT has_column_privilege(current_user,'mf_monthly_facts.sources','content','SELECT') AS allowed"
    ).fetchone()["allowed"]
    if (
        raw_allowed
        and conn.execute(
            "SELECT 1 FROM mf_monthly_facts.sources WHERE encode(sha256(content),'hex')<>content_hash OR octet_length(content)<>byte_length LIMIT 1"
        ).fetchone()
    ):
        raise ValueError("retained monthly original integrity differs")
    result["original_byte_integrity_checked"] = raw_allowed
    return result


def install(conn, root: Path):
    from mfdataindia.review.preservation import capture_preservation, compare_preservation_structure
    from mfdataindia.review.publication_operations import capture_review, compare_review

    with conn.transaction():
        who = conn.execute(
            "SELECT session_user AS name,current_user=session_user AS direct"
        ).fetchone()
        if not who["direct"] or who["name"] not in ("postgres", "mfdata_admin"):
            raise ValueError("direct monthly migration owner required")
        if (
            conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            != "repeatable read"
        ):
            raise ValueError("consistent monthly installation required")
        acquire_nav_data_operation_lock(conn)
        if capture(conn) is not None:
            return {"status": "ALREADY_INSTALLED", "source_rows_changed": 0}
        before, prior = (
            capture_preservation(conn, current_facts_baseline=True, structure_only=True),
            capture_review(conn),
        )
        policy = engine.policy()
        review._insert_exact(
            conn,
            "policies",
            "policy_hash",
            fingerprint(policy),
            policy,
            {"policy_id": policy["id"], "policy_version": policy["version"]},
        )
        ddl = (root / "sql/optional" / MIGRATION).read_text()
        if (
            hashlib.sha256(ddl.encode()).hexdigest() != SCHEMA_SHA256
            or ddl.count("\nBEGIN;\n") != 1
            or not ddl.rstrip().endswith("COMMIT;")
        ):
            raise ValueError("monthly migration differs from reviewed bytes")
        conn.execute(ddl.replace("\nBEGIN;\n", "\n", 1).rstrip()[: -len("COMMIT;")])
        for role in evidence.ROLES:
            verify_acl(conn, principal=role)
        compare_preservation_structure(
            before, capture_preservation(conn, current_facts_baseline=True, structure_only=True)
        )
        compare_review(prior, capture_review(conn), added_policy=fingerprint(policy))
        return {
            "status": "INSTALLED",
            "source_rows_changed": 0,
            "policy_hash": fingerprint(policy),
            "source_structure_preserved": True,
            "whole_source_preservation_requires_post_installation_certification": True,
        }


def publish(conn, selection: schemas.Selection, content: bytes):
    with conn.transaction():
        if (
            conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            != "read committed"
        ):
            raise ValueError("monthly append requires READ COMMITTED")
        verify_runtime(conn, "generator")
        acquire_nav_data_operation_lock(conn)
        verify_acl(conn)
        if (
            not isinstance(content, bytes)
            or not content.startswith(b"%PDF-")
            or not 0 < len(content) <= monthly_pdf.policy()["maximum_source_bytes"]
            or hashlib.sha256(content).hexdigest() != selection.intake.expected_sha256
        ):
            raise ValueError("original monthly source bytes differ")
        registry = evidence.get_document(conn, selection.identity_document_id)
        intake = selection.intake.model_dump(mode="json")
        if (
            registry["intake"]["media_type"] != "application/pdf"
            or registry["intake"]["amc"] != facts.AMC
            or not registry["intake"]["final_url"].startswith(
                "https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_"
            )
            or not set(selection.intake.codes) <= set(registry["intake"]["codes"])
        ):
            raise ValueError("retained issuer identity source differs")
        raw_registry = bytes(
            conn.execute(
                "SELECT content FROM mf_enrichment.blobs WHERE content_hash=%s",
                (registry["content_hash"],),
            ).fetchone()["content"]
        )
        if hashlib.sha256(raw_registry).hexdigest() != registry["content_hash"]:
            raise ValueError("retained identity original differs")
        text, identity_lineage = facts.decode(raw_registry, selection.identity_page)
        extracted = monthly_pdf.extract(
            content, start=selection.detail_page, end=selection.changes_page
        )
        conn.execute(
            "INSERT INTO mf_monthly_facts.sources VALUES(%s,%s,%s,%s) ON CONFLICT(content_hash) DO NOTHING",
            (selection.intake.expected_sha256, content, len(content), Jsonb(intake)),
        )
        retained = conn.execute(
            "SELECT content,intake FROM mf_monthly_facts.sources WHERE content_hash=%s",
            (selection.intake.expected_sha256,),
        ).fetchone()
        # A replay may have a later observation time, never a changed date,
        # authority, identity scope or official source URL hidden under one hash.
        if bytes(retained["content"]) != content or {
            k: v for k, v in retained["intake"].items() if k != "observed_at"
        } != {k: v for k, v in intake.items() if k != "observed_at"}:
            raise review.Conflict("retained monthly source provenance differs; no overwrite")

        def lineage(index):
            page = extracted["pages"][index]
            return {
                "extractor_version": extracted["extractor_version"],
                "extraction_policy_hash": extracted["policy_hash"],
                "extraction_engine_hash": extracted["engine_hash"],
                "text_hash": page["text_hash"],
                "page": page["page"],
            }

        result = []
        for code in sorted(selection.intake.codes):
            identity = evidence.identity(conn, code)
            derived = engine.parse(
                identity,
                extracted["pages"][0]["text"],
                extracted["pages"][1]["text"],
                text,
                as_of=selection.intake.document_as_of.isoformat(),
                detail_lineage=lineage(0),
                changes_lineage=lineage(1),
                identity_lineage=identity_lineage,
            )
            body = {
                "format": "official-monthly-fund-facts-v1",
                "code": code,
                "identity": identity,
                "identity_hash": fingerprint(identity),
                "content_hash": selection.intake.expected_sha256,
                "identity_document_id": selection.identity_document_id,
                "identity_content_hash": registry["content_hash"],
                "source_url": selection.intake.final_url,
                "identity_source_url": registry["intake"]["final_url"],
                "disclosure_as_of": selection.intake.document_as_of.isoformat(),
                **derived,
            }
            snapshot = schemas.MonthlySnapshot.model_validate(
                {**body, "snapshot_id": fingerprint(body)}
            ).model_dump(mode="json")
            conn.execute(
                "INSERT INTO mf_monthly_facts.snapshots VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    snapshot["snapshot_id"],
                    code,
                    snapshot["content_hash"],
                    snapshot["identity_document_id"],
                    snapshot["policy_hash"],
                    snapshot["disclosure_as_of"],
                    snapshot["identity_hash"],
                    Jsonb(snapshot),
                ),
            )
            row = conn.execute(
                "SELECT document FROM mf_monthly_facts.snapshots WHERE snapshot_id=%s",
                (snapshot["snapshot_id"],),
            ).fetchone()
            if not row or row["document"] != snapshot:
                raise review.Conflict(
                    "retained monthly snapshot differs; new policy/evidence required"
                )
            result.append(snapshot)
        return result


def get(conn, code, *, snapshot_id=None):
    identity = evidence.identity(conn, code)
    verify_acl(conn)
    base = {
        "format": "official-monthly-fund-facts-response-v1",
        "code": code,
        "state": "not_captured",
        "snapshot": None,
        "conflicting_snapshot_ids": [],
        "original_records_replaced": False,
        "canonical_analytics_replaced": False,
        "current_core_projection_includes_monthly_snapshot": False,
        "freshness_certified": False,
        "universe_complete": False,
    }
    if snapshot_id:
        rows = conn.execute(
            "SELECT document FROM mf_monthly_facts.snapshots WHERE amfi_scheme_code=%s AND snapshot_id=%s",
            (code, snapshot_id),
        ).fetchall()
        if not rows:
            raise review.NotFound()
    else:
        rows = conn.execute(
            "SELECT document FROM mf_monthly_facts.snapshots WHERE amfi_scheme_code=%s AND disclosure_as_of=(SELECT max(disclosure_as_of) FROM mf_monthly_facts.snapshots WHERE amfi_scheme_code=%s) ORDER BY snapshot_id LIMIT 101",
            (code, code),
        ).fetchall()
    if not rows:
        return base
    if len(rows) > 1:
        return {
            **base,
            "state": "conflicting",
            "conflicting_snapshot_ids": [r["document"]["snapshot_id"] for r in rows[:100]],
        }
    doc = schemas.MonthlySnapshot.model_validate(rows[0]["document"]).model_dump(mode="json")
    if doc["identity_hash"] != fingerprint(identity):
        return {**base, "state": "identity_changed"}
    included = False
    if conn.execute("SELECT to_regnamespace('mf_fact_refresh') AS ns").fetchone()["ns"]:
        from mfdataindia.enrichment import current_facts_store as current, monthly_current
        if current.configuration(conn)["policy_hash"] == fingerprint(monthly_current.policy()):
            included = current.projection_state(conn) and bool(conn.execute(
                "SELECT 1 FROM mf_live_facts.selected_fields s WHERE s.code=%s "
                "AND EXISTS(SELECT 1 FROM jsonb_array_elements(s.sources) x "
                "WHERE x->>'snapshot_id'=%s) LIMIT 1", (code, doc["snapshot_id"])
            ).fetchone())
    return {**base, "state": "available", "snapshot": doc,
        "current_core_projection_includes_monthly_snapshot": included}
