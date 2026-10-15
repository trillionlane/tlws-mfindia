"""Immutable accepted disclosure sidecars; read current and pinned historical snapshots."""
import hashlib

from psycopg.types.json import Jsonb

from mfdataindia.enrichment import facts, facts_schemas as schemas, store as evidence
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.store.locks import acquire_nav_data_operation_lock


def installed_policy(conn):
    policy = facts.policy()
    row = conn.execute("SELECT document FROM mf_review.policies WHERE policy_hash=%s", (fingerprint(policy),)).fetchone()
    if not row or row["document"] != policy:
        raise ValueError("official facts policy missing or differs")
    return policy


def verify_acl(conn, *, principal=None):
    principal = principal or conn.execute("SELECT current_user AS name").fetchone()["name"]
    if principal not in evidence.ROLES:
        raise ValueError("dedicated official facts principal required")
    evidence.verify_acl(conn, principal=principal)
    namespace = conn.execute("SELECT nspowner,pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_official_facts'").fetchone()
    if not namespace or namespace["owner"] not in ("postgres", "mfdata_admin"):
        raise ValueError("official facts namespace ownership differs")
    relations = conn.execute("SELECT relname,relowner,relkind FROM pg_class WHERE relnamespace='mf_official_facts'::regnamespace AND relkind IN ('r','p','S','v','m')").fetchall()
    if len(relations) != 1 or relations[0] != {"relname": "snapshots", "relowner": namespace["nspowner"], "relkind": "r"}:
        raise ValueError("official facts relation inventory differs")
    triggers = conn.execute("SELECT tgname,tgenabled FROM pg_trigger WHERE tgrelid='mf_official_facts.snapshots'::regclass AND NOT tgisinternal").fetchall()
    if {r["tgname"] for r in triggers} != {"official_snapshot_sealed", "official_snapshot_immutable"} or any(r["tgenabled"] != "O" for r in triggers):
        raise ValueError("official facts trigger drift")
    functions = conn.execute("SELECT proname,proowner,prosecdef FROM pg_proc WHERE pronamespace='mf_official_facts'::regnamespace").fetchall()
    if functions != [{"proname": "seal_snapshot", "proowner": namespace["nspowner"], "prosecdef": True}]:
        raise ValueError("official facts function drift")
    grants = conn.execute("""
      WITH objects AS (
       SELECT 'schema' AS kind,nspowner AS owner,nspacl AS acl FROM pg_namespace WHERE nspname='mf_official_facts'
       UNION ALL SELECT 'table',relowner,relacl FROM pg_class WHERE relnamespace='mf_official_facts'::regnamespace
       UNION ALL SELECT 'column',c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='mf_official_facts'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
       UNION ALL SELECT 'function',proowner,COALESCE(proacl,acldefault('f'::"char",proowner)) FROM pg_proc WHERE pronamespace='mf_official_facts'::regnamespace
      ) SELECT kind,COALESCE(r.rolname,'PUBLIC') AS grantee,x.privilege_type AS permission
      FROM objects CROSS JOIN LATERAL aclexplode(acl) x LEFT JOIN pg_roles r ON r.oid=x.grantee WHERE x.grantee<>owner
    """).fetchall()
    for grant in grants:
        allowed = set()
        if grant["grantee"] in evidence.ROLES:
            if grant["kind"] == "schema":
                allowed = {"USAGE"}
            elif grant["kind"] == "table":
                allowed = {"SELECT", "INSERT"} if grant["grantee"] == evidence.ROLES[0] else {"SELECT"}
        if grant["permission"] not in allowed:
            raise ValueError("official facts grants exceed capabilities")
    actual = conn.execute("SELECT has_schema_privilege(%s,'mf_official_facts','CREATE') AS ddl, has_table_privilege(%s,'mf_official_facts.snapshots','SELECT') AS read, has_table_privilege(%s,'mf_official_facts.snapshots','INSERT') OR has_any_column_privilege(%s,'mf_official_facts.snapshots','INSERT') AS append, has_table_privilege(%s,'mf_official_facts.snapshots','UPDATE,DELETE,TRUNCATE') OR has_any_column_privilege(%s,'mf_official_facts.snapshots','UPDATE') AS rewrite", (principal,) * 6).fetchone()
    if actual != {"ddl": False, "read": True, "append": principal == evidence.ROLES[0], "rewrite": False}:
        raise ValueError("effective official facts capability differs")
    installed_policy(conn)


def capture(conn):
    if not conn.execute("SELECT to_regnamespace('mf_official_facts') AS name").fetchone()["name"]:
        return None
    verify_acl(conn, principal=evidence.ROLES[1])
    digest, count = hashlib.sha256(), 0
    with conn.cursor() as cur, cur.copy("COPY (SELECT snapshot_id,encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') FROM mf_official_facts.snapshots t ORDER BY snapshot_id) TO STDOUT (FORMAT BINARY)") as stream:
        stream.set_types(["text", "text"])
        for identifier, sealed in stream.rows():
            digest.update((identifier + ":" + sealed + "\n").encode())
            count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def publish(conn, selection: schemas.Selection):
    with conn.transaction():
        if conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"] != "read committed":
            raise ValueError("official facts append requires READ COMMITTED")
        if conn.execute("SELECT current_user AS name,session_user=current_user AS direct").fetchone() != {"name": evidence.ROLES[0], "direct": True}:
            raise ValueError("direct official facts generator required")
        acquire_nav_data_operation_lock(conn)
        verify_runtime(conn, "generator")
        verify_acl(conn)
        docs = [evidence.get_document(conn, key) for key in (selection.document_id, selection.identity_document_id)]
        facts_doc, registry = docs
        if (not facts_doc["intake"]["final_url"].startswith("https://mutualfund.adityabirlacapital.com/-/media/")
                or not registry["intake"]["final_url"].startswith("https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_")
                or not facts_doc["intake"]["document_as_of"]):
            raise ValueError("unsupported issuer source or missing disclosure date")
        for doc in docs:
            if (doc["intake"]["media_type"] != "application/pdf"
                    or facts.normalized(doc["intake"]["amc"]) != facts.normalized(facts.AMC)
                    or not set(selection.codes) <= set(doc["intake"]["codes"])):
                raise ValueError("source media/AMC/cohort differs")
        originals = []
        for doc in docs:
            content = bytes(conn.execute("SELECT content FROM mf_enrichment.blobs WHERE content_hash=%s", (doc["content_hash"],)).fetchone()["content"])
            if hashlib.sha256(content).hexdigest() != doc["content_hash"]:
                raise ValueError("original content hash differs")
            originals.append(content)
        results = []
        for code in sorted(selection.codes):
            identity = evidence.identity(conn, code)
            values = facts.derive(identity, *originals, as_of=facts_doc["intake"]["document_as_of"], facts_page=selection.facts_page, identity_page=selection.identity_page)
            body = {"format": "official-fund-facts-v1", "code": code,
                    "identity": identity, "identity_hash": fingerprint(identity),
                    "document_id": selection.document_id, "identity_document_id": selection.identity_document_id,
                    "content_hash": facts_doc["content_hash"], "identity_content_hash": registry["content_hash"],
                    "source_url": facts_doc["intake"]["final_url"], "identity_source_url": registry["intake"]["final_url"],
                    "disclosure_as_of": facts_doc["intake"]["document_as_of"], **values}
            snapshot = schemas.Snapshot.model_validate({**body, "snapshot_id": fingerprint(body)}).model_dump(mode="json")
            conn.execute("INSERT INTO mf_official_facts.snapshots(snapshot_id,amfi_scheme_code,document_id,identity_document_id,policy_hash,disclosure_as_of,document) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(amfi_scheme_code,document_id,identity_document_id,policy_hash) DO NOTHING", (snapshot["snapshot_id"], code, selection.document_id, selection.identity_document_id, snapshot["policy_hash"], snapshot["disclosure_as_of"], Jsonb(snapshot)))
            retained = conn.execute("SELECT document FROM mf_official_facts.snapshots WHERE amfi_scheme_code=%s AND document_id=%s AND identity_document_id=%s AND policy_hash=%s", (code, selection.document_id, selection.identity_document_id, snapshot["policy_hash"])).fetchone()
            if retained["document"] != snapshot:
                raise review.Conflict("retained official facts differ; new evidence/policy required")
            results.append(snapshot)
        return results


def get(conn, code, *, snapshot_id=None):
    current = evidence.identity(conn, code)
    base = {"format": "official-fund-facts-response-v1", "code": code, "state": "not_captured",
            "reason": "no_accepted_disclosure", "snapshot": None, "conflicting_snapshot_ids": [],
            "selected_disclosure_as_of": None, "freshness_certified": False,
            "universe_complete": False, "originals_replaced": False}
    if snapshot_id:
        rows = conn.execute("SELECT document FROM mf_official_facts.snapshots WHERE snapshot_id=%s AND amfi_scheme_code=%s", (snapshot_id, code)).fetchall()
        if not rows:
            raise review.NotFound()
    else:
        rows = conn.execute("SELECT document FROM mf_official_facts.snapshots WHERE amfi_scheme_code=%s AND disclosure_as_of=(SELECT max(disclosure_as_of) FROM mf_official_facts.snapshots WHERE amfi_scheme_code=%s) ORDER BY snapshot_id LIMIT 101", (code, code)).fetchall()
    if not rows:
        return base
    date = rows[0]["document"]["disclosure_as_of"]
    if len(rows) > 1:
        return {**base, "state": "conflicting", "reason": "multiple_retained_disclosures_for_latest_date",
                "selected_disclosure_as_of": date,
                "conflicting_snapshot_ids": [r["document"]["snapshot_id"] for r in rows[:100]]}
    snapshot = schemas.Snapshot.model_validate(rows[0]["document"]).model_dump(mode="json")
    if snapshot["identity_hash"] != fingerprint(current):
        return {**base, "state": "identity_changed", "reason": "mapping_identity_changed",
                "selected_disclosure_as_of": date}
    return {**base, "state": "available", "reason": None, "snapshot": snapshot,
            "selected_disclosure_as_of": date}


def snapshots(conn, code, *, after="", limit=50):
    evidence.identity(conn, code)
    rows = conn.execute("SELECT snapshot_id FROM mf_official_facts.snapshots WHERE amfi_scheme_code=%s AND snapshot_id>%s ORDER BY snapshot_id LIMIT %s", (code, after, limit + 1)).fetchall()
    return {"items": [r["snapshot_id"] for r in rows[:limit]],
            "next_after": rows[limit - 1]["snapshot_id"] if len(rows) > limit else None}
