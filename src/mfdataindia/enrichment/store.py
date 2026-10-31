"""Sealed, bounded evidence append and read-only metadata/coverage projections."""

import hashlib

from psycopg.types.json import Jsonb

from mfdataindia.enrichment import evidence, schemas
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review import reader_access_profile as profiles
from mfdataindia.store.locks import acquire_nav_data_operation_lock

ROLES = ("mfdata_verified_generator", "mfdata_verified_reader", "mfdata_verified_admin")


def installed_policy(conn):
    document = evidence.policy()
    row = conn.execute(
        "SELECT document FROM mf_review.policies WHERE policy_hash=%s", (fingerprint(document),)
    ).fetchone()
    if not row or row["document"] != document:
        raise ValueError("official enrichment policy not installed or differs")
    return document


def verify_acl(conn, *, principal=None, reader_profile=profiles.RESTRICTED):
    principal = principal or conn.execute("SELECT current_user AS name").fetchone()["name"]
    profiles.verify_selection(conn, reader_profile, principal)
    if principal not in ROLES:
        raise ValueError("dedicated enrichment runtime required")
    if not conn.execute(
        "SELECT has_column_privilege(%s,'mf.amcs','amc_id','SELECT') AND has_column_privilege(%s,'mf.amcs','amfi_amc_name','SELECT') AS allowed",
        (principal, principal),
    ).fetchone()["allowed"]:
        raise ValueError("enrichment exact AMC identity read not available")
    namespace = conn.execute(
        "SELECT nspowner,pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_enrichment'"
    ).fetchone()
    if not namespace or namespace["owner"] not in ("mfdata_admin", "postgres"):
        raise ValueError("enrichment namespace ownership differs")
    inventory = conn.execute(
        "SELECT c.relname,c.relowner,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_enrichment' AND c.relkind IN('r','p','v','m','S')"
    ).fetchall()
    if {r["relname"] for r in inventory} != {"blobs", "documents", "mappings"} or any(
        r["relowner"] != namespace["nspowner"] or r["relkind"] != "r" for r in inventory
    ):
        raise ValueError("enrichment relation inventory/ownership differs")
    triggers = conn.execute(
        "SELECT t.tgname,t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_enrichment' AND NOT t.tgisinternal"
    ).fetchall()
    if {r["tgname"] for r in triggers} != {
        "document_sealed",
        "mapping_sealed",
        "blob_immutable",
        "document_immutable",
        "mapping_immutable",
    } or any(r["tgenabled"] != "O" for r in triggers):
        raise ValueError("enrichment sealing/immutability drift")
    functions = conn.execute(
        "SELECT p.proname,p.prosecdef,p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='mf_enrichment'"
    ).fetchall()
    if {r["proname"] for r in functions} != {"seal_document", "seal_mapping"} or any(
        not r["prosecdef"] or r["proowner"] != namespace["nspowner"] for r in functions
    ):
        raise ValueError("enrichment function capability or ownership drift")
    grants = conn.execute("""
        WITH objects AS (
          SELECT 'table' AS kind,c.relname AS name,c.relowner AS owner,c.relacl AS acl
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_enrichment'
          UNION ALL SELECT 'column',c.relname,c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
          JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_enrichment' AND a.attnum>0 AND NOT a.attisdropped
          UNION ALL SELECT 'function',p.proname,p.proowner,COALESCE(p.proacl,acldefault('f'::"char",p.proowner)) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='mf_enrichment'
          UNION ALL SELECT 'schema',nspname,nspowner,nspacl FROM pg_namespace WHERE nspname='mf_enrichment'
        ) SELECT kind,name,owner,COALESCE(r.rolname,'PUBLIC') AS grantee,x.privilege_type AS permission
        FROM objects CROSS JOIN LATERAL aclexplode(acl) x LEFT JOIN pg_roles r ON r.oid=x.grantee WHERE x.grantee<>owner
    """).fetchall()
    for grant in grants:
        allowed = set()
        if grant["grantee"] in ROLES:
            if grant["kind"] == "schema":
                allowed = {"USAGE"}
            elif grant["kind"] == "table":
                if (grant["name"] != "blobs" or grant["grantee"] == ROLES[0]
                        or profiles.is_broad_reader(grant["grantee"], reader_profile)):
                    allowed = {"SELECT"}
                if grant["grantee"] == ROLES[0]:
                    allowed.add("INSERT")
        if grant["owner"] != namespace["nspowner"] or grant["permission"] not in allowed:
            raise ValueError("enrichment grants exceed declared capabilities")
    if conn.execute(
        "SELECT has_schema_privilege(%s,'mf_enrichment','CREATE') AS allowed", (principal,)
    ).fetchone()["allowed"]:
        raise ValueError("enrichment runtime retains DDL")
    for table in ("blobs", "documents", "mappings"):
        name = "mf_enrichment." + table
        permissions = conn.execute(
            "SELECT has_table_privilege(%s,%s,'UPDATE,DELETE,TRUNCATE') OR has_any_column_privilege(%s,%s,'UPDATE') AS rewrite, has_table_privilege(%s,%s,'INSERT') OR has_any_column_privilege(%s,%s,'INSERT') AS append",
            (principal, name) * 4,
        ).fetchone()
        if permissions["rewrite"] or permissions["append"] != (principal == ROLES[0]):
            raise ValueError("effective enrichment write permissions differ")
    installed_policy(conn)


def identity(conn, code):
    row = conn.execute(
        "SELECT f.amfi_scheme_code AS code,a.amfi_amc_name AS amc,f.scheme_name,f.plan_type AS plan,f.option_type AS option, f.isin_growth_or_div_payout::text AS payout,f.isin_div_reinvest::text AS reinvest FROM mf.funds f JOIN mf.amcs a USING(amc_id) WHERE f.amfi_scheme_code=%s",
        (code,),
    ).fetchone()
    if not row:
        raise review.NotFound()
    originals = [row.pop(k) for k in ("payout", "reinvest")]
    isins = sorted({value.strip() for value in originals if value})
    return schemas.Identity.model_validate({**row, "isins": isins}).model_dump(mode="json")


def append(conn, intake: schemas.Intake, content: bytes):
    """One atomic artifact/cohort transaction; no partial imports or source DML."""
    with conn.transaction():
        if (
            conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            != "read committed"
        ):
            raise ValueError("enrichment append requires READ COMMITTED under source lock")
        if conn.execute(
            "SELECT current_user AS name,session_user=current_user AS direct"
        ).fetchone() != {"name": ROLES[0], "direct": True}:
            raise ValueError("direct evidence generator required")
        acquire_nav_data_operation_lock(conn)
        installed_policy(conn)
        if hashlib.sha256(content).hexdigest() != intake.expected_sha256:
            raise ValueError("original document checksum differs")
        parsed = evidence.classify(content, intake.media_type)
        identities = [identity(conn, code) for code in sorted(intake.codes)]
        candidates = evidence.mappings(parsed, identities, intake.amc)
        body = {
            "format": "official-enrichment-document-v1",
            "policy_hash": fingerprint(evidence.policy()),
            "content_hash": intake.expected_sha256,
            "byte_length": len(content),
            "intake": intake.model_dump(mode="json"),
            "parser_state": parsed["state"],
            "parser_reason": parsed["reason"],
            "parsed_rows": len(parsed["rows"]),
            "publication": "evidence_only_not_accepted_facts",
        }
        document = schemas.Document.model_validate(
            {**body, "document_id": fingerprint(body)}
        ).model_dump(mode="json")
        conn.execute(
            "INSERT INTO mf_enrichment.blobs(content_hash,content) VALUES(%s,%s) ON CONFLICT(content_hash) DO NOTHING",
            (intake.expected_sha256, content),
        )
        existing = conn.execute(
            "SELECT content FROM mf_enrichment.blobs WHERE content_hash=%s",
            (intake.expected_sha256,),
        ).fetchone()
        if bytes(existing["content"]) != content:
            raise review.Conflict("retained original bytes differ")
        conn.execute(
            "INSERT INTO mf_enrichment.documents(document_id,content_hash,policy_hash,document) VALUES(%s,%s,%s,%s) ON CONFLICT(document_id) DO NOTHING",
            (
                document["document_id"],
                document["content_hash"],
                document["policy_hash"],
                Jsonb(document),
            ),
        )
        if get_document(conn, document["document_id"]) != document:
            raise review.Conflict("retained source manifest differs")
        for candidate in candidates:
            value = {"document_id": document["document_id"], "mapping": candidate}
            mid = fingerprint(value)
            conn.execute(
                "INSERT INTO mf_enrichment.mappings(mapping_id,document_id,amfi_scheme_code,document) VALUES(%s,%s,%s,%s) ON CONFLICT(document_id,amfi_scheme_code) DO NOTHING",
                (mid, document["document_id"], candidate["code"], Jsonb(value)),
            )
            row = conn.execute(
                "SELECT document FROM mf_enrichment.mappings WHERE document_id=%s AND amfi_scheme_code=%s",
                (document["document_id"], candidate["code"]),
            ).fetchone()
            if row["document"] != value:
                raise review.Conflict("retained candidate mapping differs")
        return {
            "document": document,
            "mappings": candidates,
            "source_rows_changed": 0,
            "accepted_facts_published": 0,
        }


def get_document(conn, document_id):
    row = conn.execute(
        "SELECT document FROM mf_enrichment.documents WHERE document_id=%s", (document_id,)
    ).fetchone()
    if not row:
        raise review.NotFound()
    return schemas.Document.model_validate(row["document"]).model_dump(mode="json")


def documents(conn, *, after="", limit=50):
    rows = conn.execute(
        "SELECT document_id,document FROM mf_enrichment.documents WHERE document_id>%s ORDER BY document_id LIMIT %s",
        (after, limit + 1),
    ).fetchall()
    return {
        "items": [
            schemas.Document.model_validate(r["document"]).model_dump(mode="json")
            for r in rows[:limit]
        ],
        "next_after": rows[limit - 1]["document_id"] if len(rows) > limit else None,
    }


def coverage(conn, code, *, document_id=None):
    current = identity(conn, code)
    row = conn.execute(
        "SELECT m.document,d.document AS manifest FROM mf_enrichment.mappings m JOIN mf_enrichment.documents d USING(document_id) WHERE m.amfi_scheme_code=%s AND (%s::text IS NULL OR m.document_id=%s) ORDER BY (d.document->'intake'->>'observed_at')::timestamptz DESC,d.document_id DESC LIMIT 1",
        (code, document_id, document_id),
    ).fetchone()
    base = {
        "format": "official-enrichment-coverage-v1",
        "code": code,
        "policy_hash": fingerprint(evidence.policy()),
        "source_document_as_of": None,
        "document_id": None,
        "mapping": None,
        "state": "not_captured",
        "reason": "no_source_evidence",
        "facts_available": False,
        "universe_complete": False,
    }
    if not row:
        return base
    candidate = schemas.Mapping.model_validate(row["document"]["mapping"]).model_dump(mode="json")
    stale = candidate["identity_hash"] != fingerprint(current)
    return {
        **base,
        "document_id": row["document"]["document_id"],
        "mapping": candidate,
        "source_document_as_of": row["manifest"]["intake"]["document_as_of"],
        "state": "identity_changed" if stale else candidate["state"],
        "reason": "mapping_identity_changed" if stale else candidate["reason"],
    }
