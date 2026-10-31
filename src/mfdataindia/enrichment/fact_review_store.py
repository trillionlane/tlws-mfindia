"""Source-version-bound append-only human reviews and retained source differences."""

import hashlib
from pathlib import Path
from psycopg.types.json import Jsonb
from mfdataindia.enrichment import fact_review_policy, store as evidence
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review import reader_access_profile as profiles
from mfdataindia.review.privileges import verify_principal
from mfdataindia.store.locks import acquire_nav_data_operation_lock

MIGRATION = "036_official_fact_verification.sql"
SCHEMA_SHA256 = "ac679dcfaae317a195b352c08b8319d325bf43df3ea8e54d71d376ed65f059f3"


def installed(conn):
    return bool(conn.execute("SELECT to_regnamespace('mf_fact_review') AS ns").fetchone()["ns"])


def verify_acl(conn, *, principal=None, reader_profile=profiles.RESTRICTED):
    principal = principal or conn.execute("SELECT current_user AS name").fetchone()["name"]
    profiles.verify_selection(conn, reader_profile, principal)
    if principal not in evidence.ROLES:
        raise ValueError("dedicated verification principal required")
    verify_principal(conn, "generator" if principal == evidence.ROLES[0] else "api", principal, **profiles.guard_options(reader_profile))
    ns = conn.execute(
        "SELECT nspowner,pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_fact_review'"
    ).fetchone()
    if not ns or ns["owner"] not in ("postgres", "mfdata_admin"):
        raise ValueError("verification namespace owner differs")
    objects = conn.execute(
        "SELECT relname,relkind,relowner FROM pg_class WHERE relnamespace='mf_fact_review'::regnamespace AND relkind IN('r','p','m','v','S')"
    ).fetchall()
    if objects != [{"relname": "events", "relkind": "r", "relowner": ns["nspowner"]}]:
        raise ValueError("verification object inventory differs")
    triggers = conn.execute(
        "SELECT tgname,tgenabled FROM pg_trigger WHERE tgrelid='mf_fact_review.events'::regclass AND NOT tgisinternal"
    ).fetchall()
    if triggers != [{"tgname": "fact_review_events_immutable", "tgenabled": "O"}]:
        raise ValueError("verification immutability differs")
    for role in evidence.ROLES:
        state = conn.execute(
            "SELECT has_table_privilege(%s,'mf_fact_review.events','SELECT') AS read,has_table_privilege(%s,'mf_fact_review.events','INSERT,UPDATE,DELETE,TRUNCATE') OR has_any_column_privilege(%s,'mf_fact_review.events','INSERT,UPDATE') AS write,has_schema_privilege(%s,'mf_fact_review','CREATE') AS ddl,has_function_privilege(%s,'mf_fact_review.dependencies(integer)','EXECUTE') AS dependencies,has_function_privilege(%s,'mf_fact_review.record_review(integer,text,text,text,jsonb)','EXECUTE') AS review",
            (role,) * 6,
        ).fetchone()
        if state != {
            "read": role != evidence.ROLES[0],
            "write": False,
            "ddl": False,
            "dependencies": role != evidence.ROLES[0],
            "review": role == evidence.ROLES[2],
        }:
            raise ValueError("verification effective privileges differ")
    funcs = conn.execute(
        "SELECT proname,proowner,prosecdef,proconfig FROM pg_proc WHERE pronamespace='mf_fact_review'::regnamespace ORDER BY proname"
    ).fetchall()
    if funcs != [
        {
            "proname": name,
            "proowner": ns["nspowner"],
            "prosecdef": True,
            "proconfig": ["search_path=pg_catalog"],
        }
        for name in ("dependencies", "record_review")
    ]:
        raise ValueError("verification function boundary differs")
    grants = conn.execute("""WITH objects AS (
      SELECT relowner AS owner,relacl AS acl FROM pg_class WHERE relnamespace='mf_fact_review'::regnamespace
      UNION ALL SELECT c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='mf_fact_review'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
      UNION ALL SELECT nspowner,nspacl FROM pg_namespace WHERE nspname='mf_fact_review'
      UNION ALL SELECT proowner,COALESCE(proacl,acldefault('f'::"char",proowner)) FROM pg_proc WHERE pronamespace='mf_fact_review'::regnamespace
    ) SELECT pg_get_userbyid(x.grantee) AS principal,x.privilege_type FROM objects CROSS JOIN LATERAL aclexplode(acl) x WHERE x.grantee<>owner""").fetchall()
    if any(
        r["principal"] not in evidence.ROLES[1:]
        or r["privilege_type"] not in {"SELECT", "USAGE", "EXECUTE"}
        for r in grants
    ):
        raise ValueError("verification grants escape dedicated roles")
    policy = fact_review_policy.policy()
    row = conn.execute(
        "SELECT document FROM mf_review.policies WHERE policy_hash=%s", (fingerprint(policy),)
    ).fetchone()
    if not row or row["document"] != policy:
        raise ValueError("verification policy missing or differs")


def capture(conn, *, reader_profile=profiles.RESTRICTED):
    if not installed(conn):
        return None
    verify_acl(conn, principal=evidence.ROLES[1], **profiles.guard_options(reader_profile))
    rows = conn.execute(
        "SELECT event_id,encode(sha256(convert_to(to_jsonb(e)::text,'UTF8')),'hex') AS sha256 FROM mf_fact_review.events e ORDER BY event_id"
    ).fetchall()
    return {
        "events": {
            "rows": len(rows),
            "sha256": fingerprint(rows),
            "row_hashes": {r["event_id"]: r["sha256"] for r in rows},
        },
        "function_hash": fingerprint(
            conn.execute(
                "SELECT pg_get_functiondef(oid) AS definition FROM pg_proc WHERE pronamespace='mf_fact_review'::regnamespace ORDER BY proname"
            ).fetchall()
        ),
    }


def install(conn, root: Path):
    from mfdataindia.review.preservation import capture_preservation, compare_preservation_structure
    from mfdataindia.review.publication_operations import capture_review, compare_review

    with conn.transaction():
        who = conn.execute(
            "SELECT session_user AS name,current_user=session_user AS direct"
        ).fetchone()
        if not who["direct"] or who["name"] not in ("postgres", "mfdata_admin"):
            raise ValueError("direct verification migration owner required")
        if (
            conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            != "repeatable read"
        ):
            raise ValueError("consistent verification installation required")
        acquire_nav_data_operation_lock(conn)
        if capture(conn) is not None:
            return {"status": "ALREADY_INSTALLED", "source_rows_changed": 0}
        before = capture_preservation(conn, current_facts_baseline=True, structure_only=True)
        prior = capture_review(conn)
        policy = fact_review_policy.policy()
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
            raise ValueError("verification schema pin differs")
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
            "whole_source_preservation_requires_post_installation_certification": True,
        }


def dependencies(conn, code):
    if evidence.identity(conn, code) is None:
        raise review.NotFound()
    doc = conn.execute("SELECT mf_fact_review.dependencies(%s) AS document", (code,)).fetchone()[
        "document"
    ]
    return doc, fingerprint(doc)


def state(conn, code, *, after_version=0, limit=50):
    # Validate the restricted reader boundary, including during owner-side
    # projection/recovery checks. SQL still enforces the caller's own grants;
    # this does not grant the generator access to human review evidence.
    verify_acl(conn, principal=evidence.ROLES[1])
    doc, checksum = dependencies(conn, code)
    head = (
        conn.execute(
            "SELECT max(version) AS version FROM mf_fact_review.events WHERE amfi_scheme_code=%s",
            (code,),
        ).fetchone()["version"]
        or 0
    )
    latest = conn.execute(
        "SELECT DISTINCT ON(field) field,e.document FROM mf_fact_review.events e CROSS JOIN LATERAL jsonb_array_elements_text(e.document->'request'->'fields') field WHERE amfi_scheme_code=%s ORDER BY field,version DESC",
        (code,),
    ).fetchall()
    per_field = {
        field: {"status": "needs_verification", "event_id": None, "version": None}
        for field in doc["fields"]
    }
    for row in latest:
        event = row["document"]
        per_field[row["field"]] = {
            "status": "needs_revalidation"
            if event["dependency_hash"] != checksum
            else "verified"
            if event["request"]["decision"] == "verified"
            else "needs_verification",
            "event_id": event["event_id"],
            "version": event["version"],
        }
    events = conn.execute(
        "SELECT document,recorded_at FROM mf_fact_review.events WHERE amfi_scheme_code=%s AND version>%s ORDER BY version LIMIT %s",
        (code, after_version, limit),
    ).fetchall()
    return {
        "format": "official-facts-verification-state-v1",
        "code": code,
        "version": head,
        "dependency_hash": checksum,
        "dependencies": doc,
        "fields": per_field,
        "policy_hash": fingerprint(fact_review_policy.policy()),
        "history": [{**r["document"], "recorded_at": r["recorded_at"].isoformat()} for r in events],
        "next_after_version": events[-1]["document"]["version"] if len(events) == limit else None,
        "source_rows_changed": 0,
        "publication_requires_human_verification": False,
    }


def record(conn, code, request, *, caller, actor, idempotency_key):
    if (
        conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
        != "read committed"
    ):
        raise ValueError("review mutation requires READ COMMITTED")
    verify_acl(conn)
    return conn.execute(
        "SELECT mf_fact_review.record_review(%s,%s,%s,%s,%s) AS document",
        (code, caller, actor, idempotency_key, Jsonb(request)),
    ).fetchone()["document"]


def history(conn, code, *, after_id=None, limit=50):
    evidence.identity(conn, code)
    if (
        after_id
        and not conn.execute(
            "SELECT 1 FROM mf_monthly_facts.snapshots WHERE amfi_scheme_code=%s AND snapshot_id=%s",
            (code, after_id),
        ).fetchone()
    ):
        raise review.NotFound()
    rows = conn.execute(
        "SELECT snapshot_id,document,EXISTS(SELECT 1 FROM mf_fact_refresh.publications p WHERE p.snapshot_ids ? s.snapshot_id) AS published FROM mf_monthly_facts.snapshots s WHERE amfi_scheme_code=%s AND (%s::text IS NULL OR snapshot_id>%s) ORDER BY snapshot_id LIMIT %s",
        (code, after_id, after_id, limit),
    ).fetchall()
    return {
        "format": "official-facts-source-history-v1",
        "code": code,
        "snapshots": [{"snapshot": r["document"], "published": r["published"]} for r in rows],
        "next_after_id": rows[-1]["snapshot_id"] if len(rows) == limit else None,
        "order": "immutable_snapshot_id_not_recency",
        "originals_retained": True,
        "universe_complete": False,
    }


def changes(conn, code, from_id, to_id):
    evidence.identity(conn, code)
    rows = conn.execute(
        "SELECT snapshot_id,document FROM mf_monthly_facts.snapshots WHERE amfi_scheme_code=%s AND snapshot_id=ANY(%s)",
        (code, list({from_id, to_id})),
    ).fetchall()
    docs = {r["snapshot_id"]: r["document"] for r in rows}
    if set(docs) != {from_id, to_id}:
        raise review.NotFound()
    before, after = docs[from_id], docs[to_id]
    if before["identity_hash"] != after["identity_hash"]:
        raise ValueError("cross-identity source differences are not comparable")
    fields = {
        key: {"before": before["fields"].get(key), "after": after["fields"].get(key)}
        for key in sorted(set(before["fields"]) | set(after["fields"]))
        if any(
            (before["fields"].get(key) or {}).get(k) != (after["fields"].get(key) or {}).get(k)
            for k in ("value", "status", "unit", "scope", "reason")
        )
    }
    return {
        "format": "official-facts-source-changes-v1",
        "code": code,
        "from_snapshot_id": from_id,
        "to_snapshot_id": to_id,
        "from_disclosure_as_of": before["disclosure_as_of"],
        "to_disclosure_as_of": after["disclosure_as_of"],
        "fields": fields,
        "source_rows_changed": 0,
        "changes_applied": False,
        "human_verification_inferred": False,
    }
