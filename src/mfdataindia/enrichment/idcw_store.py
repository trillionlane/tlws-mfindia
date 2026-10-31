"""Append-only reported observations; authenticated snapshot-consistent reads."""

from psycopg.types.json import Jsonb
from mfdataindia.enrichment import idcw_catalog as engine, distribution_store as legacy
from mfdataindia.enrichment import portfolio_store, store as evidence
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review import reader_access_profile as profiles


def installed_policy(conn):
    document = engine.policy()
    row = conn.execute(
        "SELECT document FROM mf_review.policies WHERE policy_hash=%s", (fingerprint(document),)
    ).fetchone()
    if not row or row["document"] != document:
        raise ValueError("idcw observation policy missing or differs")
    return document


def verify_acl(conn, *, principal=None, reader_profile=profiles.RESTRICTED):
    principal = principal or conn.execute("SELECT current_user AS name").fetchone()["name"]
    profiles.verify_selection(conn, reader_profile, principal)
    legacy.verify_acl(conn, principal=principal, **profiles.guard_options(reader_profile))
    ns = conn.execute(
        "SELECT nspowner,pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_idcw'"
    ).fetchone()
    if not ns or ns["owner"] not in ("postgres", "mfdata_admin"):
        raise ValueError("idcw namespace owner differs")
    rels = conn.execute(
        "SELECT relname,relkind,relowner FROM pg_class WHERE relnamespace='mf_idcw'::regnamespace AND relkind IN('r','p','v','m','S')"
    ).fetchall()
    if {r["relname"] for r in rels} != {"sources", "snapshots"} or any(
        r["relkind"] != "r" or r["relowner"] != ns["nspowner"] for r in rels
    ):
        raise ValueError("idcw inventory differs")
    triggers = conn.execute(
        "SELECT tgname,tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='mf_idcw'::regnamespace AND NOT tgisinternal"
    ).fetchall()
    if {r["tgname"] for r in triggers} != {
        "idcw_source_sealed",
        "idcw_snapshot_sealed",
        "idcw_source_immutable",
        "idcw_snapshot_immutable",
    } or any(r["tgenabled"] != "O" for r in triggers):
        raise ValueError("idcw trigger drift")
    funcs = conn.execute(
        "SELECT proname,prosecdef,proowner FROM pg_proc WHERE pronamespace='mf_idcw'::regnamespace"
    ).fetchall()
    if {r["proname"] for r in funcs} != {"seal_source", "seal_snapshot"} or any(
        not r["prosecdef"] or r["proowner"] != ns["nspowner"] for r in funcs
    ):
        raise ValueError("idcw function drift")
    grants = conn.execute("""WITH objects AS (
      SELECT 'schema' AS kind,'' AS name,'' AS col,nspowner AS owner,nspacl AS acl FROM pg_namespace WHERE nspname='mf_idcw'
      UNION ALL SELECT 'table',relname,'',relowner,relacl FROM pg_class WHERE relnamespace='mf_idcw'::regnamespace
      UNION ALL SELECT 'column',c.relname,a.attname,c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='mf_idcw'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
      UNION ALL SELECT 'function',proname,'',proowner,COALESCE(proacl,acldefault('f'::"char",proowner)) FROM pg_proc WHERE pronamespace='mf_idcw'::regnamespace)
      SELECT kind,name,col,COALESCE(r.rolname,'PUBLIC') AS grantee,x.privilege_type AS permission FROM objects CROSS JOIN LATERAL aclexplode(acl) x LEFT JOIN pg_roles r ON r.oid=x.grantee WHERE x.grantee<>owner""").fetchall()
    for grant in grants:
        allowed = set()
        if grant["grantee"] in evidence.ROLES:
            if grant["kind"] == "schema":
                allowed = {"USAGE"}
            elif grant["kind"] == "table" and (
                grant["name"] == "snapshots" or grant["grantee"] == evidence.ROLES[0]
                or profiles.verified_reader_select(conn, grant["grantee"], reader_profile)
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
            raise ValueError("idcw grants exceed declared capabilities")
    for table in ("sources", "snapshots"):
        actual = conn.execute(
            "SELECT has_table_privilege(%s,%s,'INSERT') OR has_any_column_privilege(%s,%s,'INSERT') AS append, has_table_privilege(%s,%s,'UPDATE,DELETE,TRUNCATE') OR has_any_column_privilege(%s,%s,'UPDATE') AS rewrite",
            (principal, "mf_idcw." + table) * 4,
        ).fetchone()
        if actual != {"append": principal == evidence.ROLES[0], "rewrite": False}:
            raise ValueError("effective idcw privileges differ")
    if conn.execute(
        "SELECT has_schema_privilege(%s,'mf_idcw','CREATE') AS ddl,has_column_privilege(%s,'mf_idcw.sources','content','SELECT') AS raw",
        (principal, principal),
    ).fetchone() != {"ddl": False, "raw": profiles.source_read_expected(principal, reader_profile)}:
        raise ValueError("idcw source/DDL privileges differ")
    installed_policy(conn)


def capture(conn, *, reader_profile=profiles.RESTRICTED):
    if not conn.execute("SELECT to_regnamespace('mf_idcw') AS name").fetchone()["name"]:
        return None
    verify_acl(conn, principal=evidence.ROLES[1], **profiles.guard_options(reader_profile))
    output = {}
    for table, key in (("sources", "source_id"), ("snapshots", "snapshot_id")):
        rows = conn.execute(
            f"SELECT {key} AS id,encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS hash FROM mf_idcw.{table} t ORDER BY {key}"
        ).fetchall()
        output[table] = {
            "rows": len(rows),
            "sha256": fingerprint(rows),
            "row_hashes": {r["id"]: r["hash"] for r in rows},
        }
    return output


def append_batch(conn, requests, sources):
    if not 1 <= len(requests) <= 4 or len(sources) != len(requests):
        raise ValueError("bounded request/source batch required")
    if [r.identity.code for r in requests] != sorted({r.identity.code for r in requests}):
        raise ValueError("distinct sorted fund requests required")
    documents = [engine.derive(r, raw) for r, raw in zip(requests, sources, strict=True)]
    # Parse the entire batch before writes, then publish all or none. The shared
    # operation lock excludes ingestion/identity races until commit.
    with conn.transaction():
        portfolio_store.generator(conn)
        verify_acl(conn)
        for document in documents:
            if evidence.identity(conn, document["code"]) != document["identity"]:
                raise review.Conflict("current identity differs from issuer evidence")
        for request, document, raw in zip(requests, documents, sources, strict=True):
            keys = []
            for role in ("history", "registry"):
                metadata = {"role": role, "source": getattr(request, role).model_dump(mode="json")}
                key = fingerprint(metadata)
                conn.execute(
                    "INSERT INTO mf_idcw.sources VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (key, raw[role], Jsonb(metadata)),
                )
                found = conn.execute(
                    "SELECT content,metadata FROM mf_idcw.sources WHERE source_id=%s", (key,)
                ).fetchone()
                if (
                    not found
                    or bytes(found["content"]) != raw[role]
                    or found["metadata"] != metadata
                ):
                    raise review.Conflict("retained IDCW original differs")
                keys.append(key)
            conn.execute(
                "INSERT INTO mf_idcw.snapshots VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    document["snapshot_id"],
                    document["code"],
                    *keys,
                    document["policy_hash"],
                    Jsonb(document),
                ),
            )
            found = conn.execute(
                "SELECT document FROM mf_idcw.snapshots WHERE snapshot_id=%s",
                (document["snapshot_id"],),
            ).fetchone()
            if not found or found["document"] != document:
                raise review.Conflict("retained IDCW snapshot differs")
    return documents


def _documents(conn, code):
    rows = conn.execute(
        "SELECT document FROM mf_idcw.snapshots WHERE amfi_scheme_code=%s ORDER BY snapshot_id LIMIT 101",
        (code,),
    ).fetchall()
    if len(rows) > 100:
        return None
    output = []
    for row in rows:
        doc = engine.IdcwSnapshot.model_validate(row["document"]).model_dump(mode="json")
        if fingerprint({k: v for k, v in doc.items() if k != "snapshot_id"}) != doc["snapshot_id"]:
            raise ValueError("IDCW snapshot seal differs")
        output.append(doc)
    return output


def coverage(conn, code):
    from mfdataindia.enrichment.distributions import conflicts

    current = evidence.identity(conn, code)
    base = {
        "code": code,
        "state": "not_captured",
        "reason": "supported_but_no_v2_observations_captured",
        "coverage_state": "unknown",
        "analytics_eligible": False,
        "complete_event_window_proven": False,
        "freshness_certified": False,
        "universe_complete": False,
        "observed_record_dates": None,
        "conflicting_record_dates": [],
        "snapshots": [],
        "legacy_snapshot_ids": [],
    }
    if code not in engine.COHORT:
        return {**base, "state": "unsupported", "reason": "no_reviewed_adapter_for_this_identity"}
    documents = _documents(conn, code)
    previous = legacy._documents(conn, code) if code == 101763 else []
    if documents is None or previous is None:
        return {
            **base,
            "state": "too_many_snapshots",
            "reason": "bounded_inventory_exceeded_no_latest_selection",
        }
    if current != engine.declared_identity(code) or any(
        d["identity"] != current for d in documents + previous
    ):
        return {
            **base,
            "state": "identity_changed",
            "reason": "identity_drift_observations_withheld",
        }
    if not documents:
        return {**base, "legacy_snapshot_ids": [d["snapshot_id"] for d in previous]}
    conflicting = conflicts(documents + previous)
    return {
        **base,
        "state": "conflicting_evidence" if conflicting else "observed",
        "reason": "conflicting_assertions_retained_not_resolved"
        if conflicting
        else "reported_observations_not_complete_history",
        "observed_record_dates": sorted({e["record_date"] for d in documents for e in d["events"]}),
        "conflicting_record_dates": conflicting,
        "legacy_snapshot_ids": [d["snapshot_id"] for d in previous],
        "snapshots": [
            {
                "snapshot_id": d["snapshot_id"],
                "window": d["window"],
                "observed_event_count": len(d["events"]),
                "policy_hash": d["policy_hash"],
            }
            for d in documents
        ],
    }


def get(conn, code, snapshot_id):
    row = conn.execute(
        "SELECT document FROM mf_idcw.snapshots WHERE amfi_scheme_code=%s AND snapshot_id=%s",
        (code, snapshot_id),
    ).fetchone()
    if not row:
        raise review.NotFound()
    doc = engine.IdcwSnapshot.model_validate(row["document"]).model_dump(mode="json")
    if fingerprint({k: v for k, v in doc.items() if k != "snapshot_id"}) != snapshot_id:
        raise ValueError("IDCW snapshot seal differs")
    status = coverage(conn, code)
    return {
        k: status[k]
        for k in (
            "code",
            "state",
            "reason",
            "coverage_state",
            "analytics_eligible",
            "conflicting_record_dates",
        )
    } | {
        "snapshot_id": snapshot_id,
        "snapshot": doc if status["state"] in ("observed", "conflicting_evidence") else None,
    }


def inventory(conn, *, after=0, limit=50):
    if (
        type(after) is not int
        or not 0 <= after <= 2147483647
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise ValueError("bounded inventory page required")
    rows = conn.execute(
        "SELECT amfi_scheme_code AS code FROM mf.funds WHERE option_type='IDCW' AND amfi_scheme_code>%s ORDER BY amfi_scheme_code LIMIT %s",
        (after, limit + 1),
    ).fetchall()
    items = []
    for row in rows[:limit]:
        code = row["code"]
        if code not in engine.COHORT:
            items.append(
                {
                    "code": code,
                    "state": "unsupported",
                    "reason": "no_reviewed_adapter_for_this_identity",
                    "snapshot_count": 0,
                    "coverage_state": "unknown",
                    "analytics_eligible": False,
                }
            )
        else:
            status = coverage(conn, code)
            items.append(
                {
                    k: status[k]
                    for k in ("code", "state", "reason", "coverage_state", "analytics_eligible")
                }
                | {
                    "snapshot_count": None
                    if status["state"] in ("identity_changed", "too_many_snapshots")
                    else len(status["snapshots"])
                }
            )
    return {
        "items": items,
        "next_after_code": rows[limit - 1]["code"] if len(rows) > limit else None,
        "population": "provider_idcw_schemes_not_certified_event_coverage",
        "coverage_state": "unknown",
        "analytics_eligible": False,
    }
