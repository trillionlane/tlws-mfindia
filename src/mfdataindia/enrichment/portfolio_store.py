"""Restricted append/replay and consistent pinned private portfolio reads."""

import hashlib
from psycopg.types.json import Jsonb
from mfdataindia.enrichment import (
    portfolios as engine,
    portfolio_schemas as schemas,
    store as evidence,
)
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.store.locks import acquire_nav_data_operation_lock


def installed_policy(conn):
    value = engine.policy()
    row = conn.execute(
        "SELECT document FROM mf_review.policies WHERE policy_hash=%s", (fingerprint(value),)
    ).fetchone()
    if not row or row["document"] != value:
        raise ValueError("portfolio policy missing or differs")
    return value


def verify_acl(conn, *, principal=None):
    principal = principal or conn.execute("SELECT current_user AS name").fetchone()["name"]
    if principal not in evidence.ROLES:
        raise ValueError("dedicated portfolio principal required")
    evidence.verify_acl(conn, principal=principal)
    ns = conn.execute(
        "SELECT nspowner,pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_portfolios'"
    ).fetchone()
    if not ns or ns["owner"] not in ("postgres", "mfdata_admin"):
        raise ValueError("portfolio namespace ownership differs")
    rels = conn.execute(
        "SELECT relname,relkind,relowner FROM pg_class WHERE relnamespace='mf_portfolios'::regnamespace AND relkind IN('r','p','v','m','S')"
    ).fetchall()
    if {r["relname"] for r in rels} != {"sources", "snapshots"} or any(
        r["relkind"] != "r" or r["relowner"] != ns["nspowner"] for r in rels
    ):
        raise ValueError("portfolio relation inventory differs")
    triggers = conn.execute(
        "SELECT tgname,tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='mf_portfolios'::regnamespace AND NOT tgisinternal"
    ).fetchall()
    if {r["tgname"] for r in triggers} != {
        "portfolio_snapshot_sealed",
        "portfolio_snapshot_immutable",
        "portfolio_source_immutable",
        "portfolio_source_sealed",
    } or any(r["tgenabled"] != "O" for r in triggers):
        raise ValueError("portfolio trigger drift")
    functions = conn.execute(
        "SELECT proname,prosecdef,proowner FROM pg_proc WHERE pronamespace='mf_portfolios'::regnamespace"
    ).fetchall()
    if {r["proname"] for r in functions} != {"seal_snapshot", "seal_source"} or any(
        not r["prosecdef"] or r["proowner"] != ns["nspowner"] for r in functions
    ):
        raise ValueError("portfolio function drift")
    grants = conn.execute("""WITH objects AS (
      SELECT 'schema' AS kind,nspname AS name,'' AS col,nspowner AS owner,nspacl AS acl FROM pg_namespace WHERE nspname='mf_portfolios'
      UNION ALL SELECT 'table',relname,'',relowner,relacl FROM pg_class WHERE relnamespace='mf_portfolios'::regnamespace
      UNION ALL SELECT 'column',c.relname,a.attname,c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='mf_portfolios'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
      UNION ALL SELECT 'function',proname,'',proowner,COALESCE(proacl,acldefault('f'::"char",proowner)) FROM pg_proc WHERE pronamespace='mf_portfolios'::regnamespace)
      SELECT kind,name,col,COALESCE(r.rolname,'PUBLIC') AS grantee,x.privilege_type AS permission FROM objects CROSS JOIN LATERAL aclexplode(acl) x LEFT JOIN pg_roles r ON r.oid=x.grantee WHERE x.grantee<>owner""").fetchall()
    for grant in grants:
        allowed = set()
        if grant["grantee"] in evidence.ROLES:
            if grant["kind"] == "schema":
                allowed = {"USAGE"}
            elif grant["kind"] == "table" and (
                grant["name"] == "snapshots" or grant["grantee"] == evidence.ROLES[0]
            ):
                allowed = (
                    {"SELECT", "INSERT"} if grant["grantee"] == evidence.ROLES[0] else {"SELECT"}
                )
            elif (
                grant["kind"] == "column"
                and grant["name"] == "sources"
                and grant["col"] in ("source_id", "content_hash", "intake")
            ):
                allowed = {"SELECT"}
        if grant["permission"] not in allowed:
            raise ValueError("portfolio grants exceed declared capabilities")
    for table in ("sources", "snapshots"):
        actual = conn.execute(
            "SELECT has_table_privilege(%s,%s,'INSERT') OR has_any_column_privilege(%s,%s,'INSERT') AS append, has_table_privilege(%s,%s,'UPDATE,DELETE,TRUNCATE') OR has_any_column_privilege(%s,%s,'UPDATE') AS rewrite",
            (principal, "mf_portfolios." + table) * 4,
        ).fetchone()
        if actual != {"append": principal == evidence.ROLES[0], "rewrite": False}:
            raise ValueError("effective portfolio write capability differs")
    if conn.execute(
        "SELECT has_schema_privilege(%s,'mf_portfolios','CREATE') AS ddl,has_column_privilege(%s,'mf_portfolios.sources','content','SELECT') AS raw",
        (principal, principal),
    ).fetchone() != {"ddl": False, "raw": principal == evidence.ROLES[0]}:
        raise ValueError("portfolio raw-source or DDL capability differs")
    installed_policy(conn)


def capture(conn):
    if not conn.execute("SELECT to_regnamespace('mf_portfolios') AS name").fetchone()["name"]:
        return None
    verify_acl(conn, principal=evidence.ROLES[1])
    output = {}
    for table, key in (("sources", "source_id"), ("snapshots", "snapshot_id")):
        # Owner or generator only: source bytes included in original-row seal.
        rows = conn.execute(
            f"SELECT {key} AS id,encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS hash FROM mf_portfolios.{table} t ORDER BY {key}"
        ).fetchall()
        output[table] = {
            "rows": len(rows),
            "sha256": fingerprint(rows),
            "row_hashes": {r["id"]: r["hash"] for r in rows},
        }
    return output


def generator(conn):
    if conn.execute("SHOW transaction_isolation").fetchone()[
        "transaction_isolation"
    ] != "read committed" or conn.execute(
        "SELECT current_user AS name,session_user=current_user AS direct"
    ).fetchone() != {"name": evidence.ROLES[0], "direct": True}:
        raise ValueError("direct READ COMMITTED portfolio generator required")
    acquire_nav_data_operation_lock(conn)
    verify_runtime(conn, "generator")
    verify_acl(conn)


def append_source(conn, intake: schemas.Intake, content: bytes):
    with conn.transaction():
        generator(conn)
        if (
            not isinstance(content, bytes)
            or not 0 < len(content) <= engine.MAX_BYTES
            or hashlib.sha256(content).hexdigest() != intake.content_hash
        ):
            raise ValueError("original source bytes/hash differ")
        if not content.startswith(
            b"PK\x03\x04" if intake.media_type == "application/zip" else b"%PDF-"
        ):
            raise ValueError("source media magic differs")
        body = intake.model_dump(mode="json")
        key = fingerprint(body)
        conn.execute(
            "INSERT INTO mf_portfolios.sources VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (key, intake.content_hash, content, Jsonb(body)),
        )
        retained = conn.execute(
            "SELECT content_hash,content,intake FROM mf_portfolios.sources WHERE source_id=%s",
            (key,),
        ).fetchone()
        if not retained or bytes(retained["content"]) != content or retained["intake"] != body:
            raise review.Conflict("retained portfolio source differs")
        return key


def publish(conn, selection: schemas.Selection):
    with conn.transaction():
        generator(conn)
        sources = []
        for key in (selection.source_id, selection.identity_source_id):
            row = conn.execute(
                "SELECT content_hash,content,intake FROM mf_portfolios.sources WHERE source_id=%s",
                (key,),
            ).fetchone()
            if not row:
                raise review.NotFound()
            if hashlib.sha256(bytes(row["content"])).hexdigest() != row["content_hash"]:
                raise ValueError("source hash differs")
            schemas.Intake.model_validate(row["intake"])
            sources.append(row)
        source, registry = sources
        if (
            source["intake"]["media_type"] != "application/zip"
            or registry["intake"]["media_type"] != "application/pdf"
            or not source["intake"]["disclosure_as_of"]
        ):
            raise ValueError("source types/date differ")
        output = []
        for target in sorted(selection.targets, key=lambda t: t.code):
            identity = evidence.identity(conn, target.code)
            result = engine.derive(
                identity,
                bytes(source["content"]),
                bytes(registry["content"]),
                as_of=source["intake"]["disclosure_as_of"],
                sheet=target.sheet,
                identity_page=target.identity_page,
            )
            body = {
                "format": "official-complete-portfolio-v1",
                "code": target.code,
                "identity": identity,
                "identity_hash": fingerprint(identity),
                "source_id": selection.source_id,
                "identity_source_id": selection.identity_source_id,
                "content_hash": source["content_hash"],
                "identity_content_hash": registry["content_hash"],
                "source_url": source["intake"]["source_url"],
                "identity_source_url": registry["intake"]["source_url"],
                "disclosure_as_of": source["intake"]["disclosure_as_of"],
                "holdings_count": len(result["holdings"]),
                **result,
            }
            document = schemas.Snapshot.model_validate(
                {**body, "snapshot_id": fingerprint(body)}
            ).model_dump(mode="json")
            conn.execute(
                "INSERT INTO mf_portfolios.snapshots VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(amfi_scheme_code,source_id,identity_source_id,policy_hash) DO NOTHING",
                (
                    document["snapshot_id"],
                    target.code,
                    selection.source_id,
                    selection.identity_source_id,
                    document["policy_hash"],
                    document["disclosure_as_of"],
                    Jsonb(document),
                ),
            )
            retained = conn.execute(
                "SELECT document FROM mf_portfolios.snapshots WHERE amfi_scheme_code=%s AND source_id=%s AND identity_source_id=%s AND policy_hash=%s",
                (
                    target.code,
                    selection.source_id,
                    selection.identity_source_id,
                    document["policy_hash"],
                ),
            ).fetchone()
            if retained["document"] != document:
                raise review.Conflict("retained portfolio differs; new evidence/policy required")
            output.append(document)
        return output


def get(conn, code, *, snapshot_id=None):
    current = evidence.identity(conn, code)
    base = {
        "code": code,
        "state": "not_captured",
        "reason": "no_accepted_complete_portfolio",
        "portfolio": None,
        "conflicting_snapshot_ids": [],
        "freshness_certified": False,
        "universe_complete": False,
        "originals_replaced": False,
    }
    if snapshot_id:
        rows = conn.execute(
            "SELECT document FROM mf_portfolios.snapshots WHERE amfi_scheme_code=%s AND snapshot_id=%s",
            (code, snapshot_id),
        ).fetchall()
        if not rows:
            raise review.NotFound()
    else:
        rows = conn.execute(
            "SELECT document FROM mf_portfolios.snapshots WHERE amfi_scheme_code=%s AND disclosure_as_of=(SELECT max(disclosure_as_of) FROM mf_portfolios.snapshots WHERE amfi_scheme_code=%s) ORDER BY snapshot_id LIMIT 101",
            (code, code),
        ).fetchall()
    if not rows:
        return base, None
    if len(rows) > 1:
        return {
            **base,
            "state": "conflicting",
            "reason": "same_date_distinct_retained_sources",
            "conflicting_snapshot_ids": [r["document"]["snapshot_id"] for r in rows[:100]],
        }, None
    document = schemas.Snapshot.model_validate(rows[0]["document"]).model_dump(mode="json")
    if document["identity_hash"] != fingerprint(current):
        return {**base, "state": "identity_changed", "reason": "current_identity_differs"}, None
    return {
        **base,
        "state": "available",
        "reason": None,
        "portfolio": {k: v for k, v in document.items() if k not in ("holdings", "notes")},
    }, document


def holdings(conn, code, snapshot_id, *, after=0, limit=50):
    result, document = get(conn, code, snapshot_id=snapshot_id)
    if result["state"] != "available":
        raise review.Conflict("portfolio identity requires revalidation")
    values = [x for x in document["holdings"] if x["row"] > after]
    return {
        "snapshot_id": snapshot_id,
        "items": values[:limit],
        "next_after_row": values[limit - 1]["row"] if len(values) > limit else None,
    }


def snapshots(conn, code, *, after="", limit=50):
    evidence.identity(conn, code)
    rows = conn.execute(
        "SELECT snapshot_id FROM mf_portfolios.snapshots WHERE amfi_scheme_code=%s AND snapshot_id>%s ORDER BY snapshot_id LIMIT %s",
        (code, after, limit + 1),
    ).fetchall()
    return {
        "items": [r["snapshot_id"] for r in rows[:limit]],
        "next_after": rows[limit - 1]["snapshot_id"] if len(rows) > limit else None,
    }
