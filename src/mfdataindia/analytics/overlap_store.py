"""Restricted immutable publication/replay and snapshot-consistent overlap reads."""

from psycopg.types.json import Jsonb
from mfdataindia.analytics import overlap as engine, overlap_schemas as schemas
from mfdataindia.enrichment import portfolio_store as portfolios
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review import reader_access_profile as profiles


def installed_policy(conn):
    value = engine.policy()
    row = conn.execute(
        "SELECT document FROM mf_review.policies WHERE policy_hash=%s", (fingerprint(value),)
    ).fetchone()
    if not row or row["document"] != value:
        raise ValueError("overlap policy missing or differs")
    return value


def verify_acl(conn, *, principal=None, reader_profile=profiles.RESTRICTED):
    principal = principal or conn.execute("SELECT current_user AS name").fetchone()["name"]
    profiles.verify_selection(conn, reader_profile, principal)
    portfolios.verify_acl(conn, principal=principal, **profiles.guard_options(reader_profile))
    ns = conn.execute(
        "SELECT nspowner,pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_overlap'"
    ).fetchone()
    if not ns or ns["owner"] not in ("postgres", "mfdata_admin"):
        raise ValueError("overlap namespace owner differs")
    rels = conn.execute(
        "SELECT relname,relkind,relowner FROM pg_class WHERE relnamespace='mf_overlap'::regnamespace AND relkind IN('r','p','v','m','S')"
    ).fetchall()
    if len(rels) != 1 or rels[0] != {
        "relname": "results",
        "relkind": "r",
        "relowner": ns["nspowner"],
    }:
        raise ValueError("overlap inventory differs")
    triggers = conn.execute(
        "SELECT tgname,tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='mf_overlap'::regnamespace AND NOT tgisinternal"
    ).fetchall()
    if {r["tgname"] for r in triggers} != {
        "overlap_result_sealed",
        "overlap_result_immutable",
    } or any(r["tgenabled"] != "O" for r in triggers):
        raise ValueError("overlap trigger drift")
    funcs = conn.execute(
        "SELECT proname,prosecdef,proowner FROM pg_proc WHERE pronamespace='mf_overlap'::regnamespace"
    ).fetchall()
    if funcs != [{"proname": "seal_result", "prosecdef": True, "proowner": ns["nspowner"]}]:
        raise ValueError("overlap function drift")
    grants = conn.execute("""WITH objects AS (
      SELECT 'schema' AS kind,nspowner AS owner,nspacl AS acl FROM pg_namespace WHERE nspname='mf_overlap'
      UNION ALL SELECT 'table',relowner,relacl FROM pg_class WHERE relnamespace='mf_overlap'::regnamespace
      UNION ALL SELECT 'column',c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='mf_overlap'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
      UNION ALL SELECT 'function',proowner,COALESCE(proacl,acldefault('f'::"char",proowner)) FROM pg_proc WHERE pronamespace='mf_overlap'::regnamespace)
      SELECT kind,COALESCE(r.rolname,'PUBLIC') AS grantee,x.privilege_type AS permission FROM objects CROSS JOIN LATERAL aclexplode(acl) x LEFT JOIN pg_roles r ON r.oid=x.grantee WHERE x.grantee<>owner""").fetchall()
    for grant in grants:
        allowed = set()
        if grant["grantee"] in portfolios.evidence.ROLES:
            if grant["kind"] == "schema":
                allowed = {"USAGE"}
            elif grant["kind"] == "table":
                allowed = {"SELECT"} | (
                    {"INSERT"} if grant["grantee"] == portfolios.evidence.ROLES[0] else set()
                )
        if grant["permission"] not in allowed:
            raise ValueError("overlap grants exceed declared capabilities")
    if conn.execute(
        "SELECT has_schema_privilege(%s,'mf_overlap','CREATE') AS ddl, has_table_privilege(%s,'mf_overlap.results','INSERT') OR has_any_column_privilege(%s,'mf_overlap.results','INSERT') AS append, has_table_privilege(%s,'mf_overlap.results','UPDATE,DELETE,TRUNCATE') OR has_any_column_privilege(%s,'mf_overlap.results','UPDATE') AS rewrite",
        (principal,) * 5,
    ).fetchone() != {
        "ddl": False,
        "append": principal == portfolios.evidence.ROLES[0],
        "rewrite": False,
    }:
        raise ValueError("effective overlap privileges differ")
    installed_policy(conn)


def capture(conn, *, reader_profile=profiles.RESTRICTED):
    if not conn.execute("SELECT to_regnamespace('mf_overlap') AS name").fetchone()["name"]:
        return None
    verify_acl(conn, principal=portfolios.evidence.ROLES[1], **profiles.guard_options(reader_profile))
    rows = conn.execute(
        "SELECT result_id AS id,encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS hash FROM mf_overlap.results t ORDER BY result_id"
    ).fetchall()
    return {
        "rows": len(rows),
        "sha256": fingerprint(rows),
        "row_hashes": {r["id"]: r["hash"] for r in rows},
    }


def inputs(conn, pair):
    values = []
    for code, key in (
        (pair.left_code, pair.left_snapshot_id),
        (pair.right_code, pair.right_snapshot_id),
    ):
        response, snapshot = portfolios.get(conn, code, snapshot_id=key)
        if response["state"] != "available":
            raise review.Conflict("portfolio identity changed")
        values.append(snapshot)
    return values


def publish(conn, pair: schemas.Pair):
    with conn.transaction():
        portfolios.generator(conn)
        verify_acl(conn)
        left, right = inputs(conn, pair)
        document = schemas.Document.model_validate(engine.derive(left, right)).model_dump(
            mode="json"
        )
        conn.execute(
            "INSERT INTO mf_overlap.results VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(left_snapshot_id,right_snapshot_id,policy_hash,engine_code_hash) DO NOTHING",
            (
                document["result_id"],
                pair.left_snapshot_id,
                pair.right_snapshot_id,
                document["policy_hash"],
                document["engine_code_hash"],
                Jsonb(document),
            ),
        )
        retained = conn.execute(
            "SELECT document FROM mf_overlap.results WHERE left_snapshot_id=%s AND right_snapshot_id=%s AND policy_hash=%s AND engine_code_hash=%s",
            (
                pair.left_snapshot_id,
                pair.right_snapshot_id,
                document["policy_hash"],
                document["engine_code_hash"],
            ),
        ).fetchone()
        if not retained or retained["document"] != document:
            raise review.Conflict("retained overlap differs")
        return document


def get(conn, left_code, right_code, *, left_snapshot_id=None, right_snapshot_id=None):
    if left_code == right_code or bool(left_snapshot_id) != bool(right_snapshot_id):
        raise ValueError("distinct codes and either two pins or no pins required")
    if left_code > right_code:
        left_code, right_code = right_code, left_code
        left_snapshot_id, right_snapshot_id = right_snapshot_id, left_snapshot_id
    mode = "explicit_historical_pins" if left_snapshot_id else "current"
    base = {"state": "inputs_unavailable", "reason": None, "selection": mode, "result": None}
    values = []
    for code, key in ((left_code, left_snapshot_id), (right_code, right_snapshot_id)):
        response, snapshot = portfolios.get(conn, code, snapshot_id=key)
        if response["state"] != "available":
            return {**base, "reason": "portfolio_" + response["state"]}, None
        values.append(snapshot)
    if values[0]["disclosure_as_of"] != values[1]["disclosure_as_of"]:
        return {**base, "reason": "portfolio_dates_differ"}, None
    row = conn.execute(
        "SELECT document FROM mf_overlap.results WHERE left_snapshot_id=%s AND right_snapshot_id=%s AND policy_hash=%s AND engine_code_hash=%s",
        (
            values[0]["snapshot_id"],
            values[1]["snapshot_id"],
            fingerprint(engine.policy()),
            engine.engine_hash(),
        ),
    ).fetchone()
    if not row:
        return {
            **base,
            "state": "not_published",
            "reason": "accepted_pair_not_generated_under_current_policy_engine",
        }, None
    document = schemas.Document.model_validate(row["document"]).model_dump(mode="json")
    if (
        fingerprint({k: v for k, v in document.items() if k != "result_id"})
        != document["result_id"]
    ):
        raise ValueError("retained overlap seal differs")
    return {
        **base,
        "state": "available",
        "result": {k: v for k, v in document.items() if k != "shared_holdings"},
    }, document


def result(conn, result_id):
    row = conn.execute(
        "SELECT document FROM mf_overlap.results WHERE result_id=%s", (result_id,)
    ).fetchone()
    if not row:
        raise review.NotFound()
    document = schemas.Document.model_validate(row["document"]).model_dump(mode="json")
    # Explicit historical route, but current identities must still match. Use
    # the retained policy/engine, not current generation pins, for old results.
    inputs(conn, schemas.Pair.model_validate({k: document[k] for k in schemas.Pair.model_fields}))
    if fingerprint({k: v for k, v in document.items() if k != "result_id"}) != result_id:
        raise ValueError("retained overlap seal differs")
    return document


def shared(conn, result_id, *, after="", limit=50):
    document = result(conn, result_id)
    rows = [r for r in document["shared_holdings"] if r["isin"] > after]
    return {
        "result_id": result_id,
        "items": rows[:limit],
        "next_after_isin": rows[limit - 1]["isin"] if len(rows) > limit else None,
    }
