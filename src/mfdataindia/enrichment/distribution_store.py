"""Append-only reported observations; authenticated snapshot-consistent reads."""

from psycopg.types.json import Jsonb
from mfdataindia.enrichment import distributions as engine, distribution_schemas as schemas
from mfdataindia.enrichment import portfolio_store, store as evidence
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint


def installed_policy(conn):
    document = engine.policy()
    row = conn.execute(
        "SELECT document FROM mf_review.policies WHERE policy_hash=%s", (fingerprint(document),)
    ).fetchone()
    if not row or row["document"] != document:
        raise ValueError("distribution observation policy missing or differs")
    return document


def verify_acl(conn, *, principal=None):
    principal = principal or conn.execute("SELECT current_user AS name").fetchone()["name"]
    portfolio_store.verify_acl(conn, principal=principal)
    ns = conn.execute(
        "SELECT nspowner,pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_distributions'"
    ).fetchone()
    if not ns or ns["owner"] not in ("postgres", "mfdata_admin"):
        raise ValueError("distribution namespace owner differs")
    rels = conn.execute(
        "SELECT relname,relkind,relowner FROM pg_class WHERE relnamespace='mf_distributions'::regnamespace AND relkind IN('r','p','v','m','S')"
    ).fetchall()
    if {r["relname"] for r in rels} != {"sources", "snapshots"} or any(
        r["relkind"] != "r" or r["relowner"] != ns["nspowner"] for r in rels
    ):
        raise ValueError("distribution inventory differs")
    triggers = conn.execute(
        "SELECT tgname,tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='mf_distributions'::regnamespace AND NOT tgisinternal"
    ).fetchall()
    if {r["tgname"] for r in triggers} != {
        "distribution_source_sealed",
        "distribution_snapshot_sealed",
        "distribution_source_immutable",
        "distribution_snapshot_immutable",
    } or any(r["tgenabled"] != "O" for r in triggers):
        raise ValueError("distribution trigger drift")
    funcs = conn.execute(
        "SELECT proname,prosecdef,proowner FROM pg_proc WHERE pronamespace='mf_distributions'::regnamespace"
    ).fetchall()
    if {r["proname"] for r in funcs} != {"seal_source", "seal_snapshot"} or any(
        not r["prosecdef"] or r["proowner"] != ns["nspowner"] for r in funcs
    ):
        raise ValueError("distribution function drift")
    grants = conn.execute("""WITH objects AS (
      SELECT 'schema' AS kind,'' AS name,'' AS col,nspowner AS owner,nspacl AS acl FROM pg_namespace WHERE nspname='mf_distributions'
      UNION ALL SELECT 'table',relname,'',relowner,relacl FROM pg_class WHERE relnamespace='mf_distributions'::regnamespace
      UNION ALL SELECT 'column',c.relname,a.attname,c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='mf_distributions'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
      UNION ALL SELECT 'function',proname,'',proowner,COALESCE(proacl,acldefault('f'::"char",proowner)) FROM pg_proc WHERE pronamespace='mf_distributions'::regnamespace)
      SELECT kind,name,col,COALESCE(r.rolname,'PUBLIC') AS grantee,x.privilege_type AS permission FROM objects CROSS JOIN LATERAL aclexplode(acl) x LEFT JOIN pg_roles r ON r.oid=x.grantee WHERE x.grantee<>owner""").fetchall()
    for grant in grants:
        allowed = set()
        if grant["grantee"] in evidence.ROLES:
            if grant["kind"] == "schema":
                allowed = {"USAGE"}
            elif grant["kind"] == "table" and (
                grant["name"] == "snapshots" or grant["grantee"] == evidence.ROLES[0]
            ):
                allowed = {"SELECT"} | (
                    {"INSERT"} if grant["grantee"] == evidence.ROLES[0] else set()
                )
            elif (
                grant["kind"] == "column"
                and grant["name"] == "sources"
                and grant["col"] in ("source_id", "metadata")
            ):
                allowed = {"SELECT"}
        if grant["permission"] not in allowed:
            raise ValueError("distribution grants exceed declared capabilities")
    for table in ("sources", "snapshots"):
        actual = conn.execute(
            "SELECT has_table_privilege(%s,%s,'INSERT') OR has_any_column_privilege(%s,%s,'INSERT') AS append, has_table_privilege(%s,%s,'UPDATE,DELETE,TRUNCATE') OR has_any_column_privilege(%s,%s,'UPDATE') AS rewrite",
            (principal, "mf_distributions." + table) * 4,
        ).fetchone()
        if actual != {"append": principal == evidence.ROLES[0], "rewrite": False}:
            raise ValueError("effective distribution privileges differ")
    if conn.execute(
        "SELECT has_schema_privilege(%s,'mf_distributions','CREATE') AS ddl,has_column_privilege(%s,'mf_distributions.sources','content','SELECT') AS raw",
        (principal, principal),
    ).fetchone() != {"ddl": False, "raw": principal == evidence.ROLES[0]}:
        raise ValueError("distribution source/DDL privileges differ")
    installed_policy(conn)


def capture(conn):
    if not conn.execute("SELECT to_regnamespace('mf_distributions') AS name").fetchone()["name"]:
        return None
    verify_acl(conn, principal=evidence.ROLES[1])
    output = {}
    for table, key in (("sources", "source_id"), ("snapshots", "snapshot_id")):
        rows = conn.execute(
            f"SELECT {key} AS id,encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS hash FROM mf_distributions.{table} t ORDER BY {key}"
        ).fetchall()
        output[table] = {
            "rows": len(rows),
            "sha256": fingerprint(rows),
            "row_hashes": {r["id"]: r["hash"] for r in rows},
        }
    return output


def append(conn, request, sources):
    # Parse before starting any write; only an exact current identity may append.
    document = schemas.DistributionSnapshot.model_validate(
        engine.derive(request, sources)
    ).model_dump(mode="json")
    with conn.transaction():
        portfolio_store.generator(conn)
        verify_acl(conn)
        if evidence.identity(conn, request.identity.code) != document["identity"]:
            raise review.Conflict("current identity differs from issuer-bound request")
        keys = []
        for role in ("history", "notice", "registry"):
            metadata = {"role": role, "source": getattr(request, role).model_dump(mode="json")}
            key = fingerprint(metadata)
            content = sources[role]
            conn.execute(
                "INSERT INTO mf_distributions.sources VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (key, content, Jsonb(metadata)),
            )
            row = conn.execute(
                "SELECT content,metadata FROM mf_distributions.sources WHERE source_id=%s", (key,)
            ).fetchone()
            if not row or bytes(row["content"]) != content or row["metadata"] != metadata:
                raise review.Conflict("retained distribution original differs")
            keys.append(key)
        conn.execute(
            "INSERT INTO mf_distributions.snapshots VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                document["snapshot_id"],
                document["code"],
                *keys,
                document["policy_hash"],
                Jsonb(document),
            ),
        )
        row = conn.execute(
            "SELECT document FROM mf_distributions.snapshots WHERE snapshot_id=%s",
            (document["snapshot_id"],),
        ).fetchone()
        if not row or row["document"] != document:
            raise review.Conflict("retained observation snapshot differs")
        return document


def _documents(conn, code):
    rows = conn.execute(
        "SELECT document FROM mf_distributions.snapshots WHERE amfi_scheme_code=%s ORDER BY snapshot_id LIMIT 101",
        (code,),
    ).fetchall()
    if len(rows) > 100:
        return None
    result = []
    for row in rows:
        doc = schemas.DistributionSnapshot.model_validate(row["document"]).model_dump(mode="json")
        if fingerprint({k: v for k, v in doc.items() if k != "snapshot_id"}) != doc["snapshot_id"]:
            raise ValueError("distribution snapshot hash differs")
        result.append(doc)
    return result


def coverage(conn, code):
    current = evidence.identity(conn, code)
    base = {
        "code": code,
        "state": "not_captured",
        "coverage_state": "unknown",
        "complete_event_window_proven": False,
        "analytics_eligible": False,
        "freshness_certified": False,
        "universe_complete": False,
        "source_data_replaced": False,
        "observed_record_dates": None,
        "conflicting_record_dates": [],
        "snapshots": [],
        "reason": "no_reported_observations_captured_not_evidence_of_no_distributions",
    }
    documents = _documents(conn, code)
    if documents is None:
        return {
            **base,
            "state": "too_many_snapshots",
            "reason": "bounded_inventory_exceeded_no_latest_selection",
        }
    if not documents:
        return base
    if any(d["identity"] != current for d in documents):
        return {
            **base,
            "state": "identity_changed",
            "reason": "retained_identity_differs_observations_withheld",
        }
    conflicting = engine.conflicts(documents)
    return {
        **base,
        "state": "conflicting_evidence" if conflicting else "observed",
        "reason": "conflicting_assertions_retained_not_resolved"
        if conflicting
        else "reported_observations_not_complete_history_or_adjusted_analytics",
        "observed_record_dates": sorted({e["record_date"] for d in documents for e in d["events"]}),
        "conflicting_record_dates": conflicting,
        "snapshots": [
            {
                "snapshot_id": d["snapshot_id"],
                "window": d["window"],
                "history_reporting_month": d["history_reporting_month"],
                "observed_event_count": len(d["events"]),
                "policy_hash": d["policy_hash"],
                "engine_code_hash": d["engine_code_hash"],
            }
            for d in documents
        ],
    }


def get(conn, code, snapshot_id):
    row = conn.execute(
        "SELECT document FROM mf_distributions.snapshots WHERE amfi_scheme_code=%s AND snapshot_id=%s",
        (code, snapshot_id),
    ).fetchone()
    if not row:
        raise review.NotFound()
    status = coverage(conn, code)
    document = schemas.DistributionSnapshot.model_validate(row["document"]).model_dump(mode="json")
    if fingerprint({k: v for k, v in document.items() if k != "snapshot_id"}) != snapshot_id:
        raise ValueError("distribution snapshot seal differs")
    return {
        "code": code,
        "snapshot_id": snapshot_id,
        "state": status["state"],
        "coverage_state": "unknown",
        "analytics_eligible": False,
        "reason": status["reason"],
        "conflicting_record_dates": status["conflicting_record_dates"],
        "snapshot": document if status["state"] in ("observed", "conflicting_evidence") else None,
    }
