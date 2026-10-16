"""Append-only registered source publication; owner-only installation and recovery."""

import hashlib

from psycopg import sql
from psycopg.types.json import Jsonb

from mfdataindia.enrichment import multi_facts, current_facts_store as current, monthly_current
from mfdataindia.enrichment import store as evidence
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_principal
from mfdataindia.store.locks import acquire_nav_data_operation_lock

MIGRATION = "037_registered_monthly_facts.sql"
SCHEMA_SHA256 = "2b5048a1f3640ea6de9845ada60e46352d2fcbe03598f4869117ef86ae2ba18a"
TABLES = {"configuration", "sources", "snapshots", "runs", "transitions"}


def installed(conn):
    return bool(conn.execute("SELECT to_regnamespace('mf_multi_facts') AS name").fetchone()["name"])


def verify_acl(conn, *, principal=None):
    principal = principal or conn.execute("SELECT current_user AS name").fetchone()["name"]
    verify_principal(conn, "generator" if principal == evidence.ROLES[0] else "api", principal)
    namespace = conn.execute(
        "SELECT nspowner,pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_multi_facts'"
    ).fetchone()
    if not namespace or namespace["owner"] not in ("postgres", "mfdata_admin"):
        raise ValueError("registered facts owner differs")
    objects = conn.execute(
        "SELECT relname,relkind,relowner FROM pg_class WHERE relnamespace='mf_multi_facts'::regnamespace AND relkind IN('r','p','v','m','S')"
    ).fetchall()
    if {r["relname"] for r in objects} != TABLES | {"prior_field_versions"} or any(
        r["relowner"] != namespace["nspowner"]
        or r["relkind"] != ("v" if r["relname"] == "prior_field_versions" else "r")
        for r in objects
    ):
        raise ValueError("registered facts object inventory differs")
    functions = conn.execute(
        "SELECT proname,prosecdef,proowner,proconfig FROM pg_proc WHERE pronamespace='mf_multi_facts'::regnamespace"
    ).fetchall()
    if functions != [
        {
            "proname": "seal_input",
            "prosecdef": True,
            "proowner": namespace["nspowner"],
            "proconfig": ["search_path=pg_catalog"],
        }
    ]:
        raise ValueError("registered facts executable boundary differs")
    triggers = conn.execute(
        "SELECT c.relname,t.tgname,t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='mf_multi_facts'::regnamespace AND NOT t.tgisinternal"
    ).fetchall()
    expected = {(t, t + "_immutable") for t in TABLES} | {
        (t, t + "_sealed") for t in ("sources", "snapshots", "runs")
    }
    if {(r["relname"], r["tgname"]) for r in triggers} != expected or any(
        r["tgenabled"] != "O" for r in triggers
    ):
        raise ValueError("registered facts sealed/immutable triggers differ")
    grants = conn.execute("""WITH acl AS (
        SELECT 'relation' AS kind,relname AS name,relowner AS owner,relacl AS acl FROM pg_class WHERE relnamespace='mf_multi_facts'::regnamespace
        UNION ALL SELECT 'column',c.relname,c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='mf_multi_facts'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
        UNION ALL SELECT 'schema',nspname,nspowner,nspacl FROM pg_namespace WHERE nspname='mf_multi_facts'
        UNION ALL SELECT 'function',proname,proowner,COALESCE(proacl,acldefault('f'::"char",proowner)) FROM pg_proc WHERE pronamespace='mf_multi_facts'::regnamespace
    ) SELECT kind,name,pg_get_userbyid(x.grantee) AS role,x.privilege_type FROM acl CROSS JOIN LATERAL aclexplode(acl.acl) x WHERE x.grantee<>owner""").fetchall()
    for g in grants:
        allowed = set()
        if g["role"] in evidence.ROLES:
            if g["kind"] == "schema":
                allowed = {"USAGE"}
            elif g["kind"] in ("relation", "column"):
                allowed = {"SELECT"}
                if g["role"] == evidence.ROLES[0] and g["name"] in ("sources", "snapshots", "runs"):
                    allowed.add("INSERT")
        if g["privilege_type"] not in allowed:
            raise ValueError("registered facts grants escape least privilege")
    for table in TABLES | {"prior_field_versions"}:
        permissions = conn.execute(
            "SELECT has_table_privilege(%s,%s,'INSERT') OR has_any_column_privilege(%s,%s,'INSERT') AS append,has_table_privilege(%s,%s,'UPDATE,DELETE,TRUNCATE') OR has_any_column_privilege(%s,%s,'UPDATE') AS rewrite",
            (principal, "mf_multi_facts." + table) * 4,
        ).fetchone()
        if permissions != {
            "append": principal == evidence.ROLES[0] and table in ("sources", "snapshots", "runs"),
            "rewrite": False,
        }:
            raise ValueError("registered facts effective write capabilities differ")
    raw = conn.execute(
        "SELECT has_column_privilege(%s,'mf_multi_facts.sources','content','SELECT') AS allowed,has_schema_privilege(%s,'mf_multi_facts','CREATE') AS ddl",
        (principal, principal),
    ).fetchone()
    if raw != {"allowed": principal == evidence.ROLES[0], "ddl": False}:
        raise ValueError("registered facts raw/DDL capabilities differ")
    cfg = conn.execute(
        "SELECT registry_hash,registry,policy_hash FROM mf_multi_facts.configuration ORDER BY generation DESC LIMIT 1"
    ).fetchone()
    if cfg != {
        "registry_hash": fingerprint(multi_facts.registry()),
        "registry": multi_facts.registry(),
        "policy_hash": fingerprint(multi_facts.policy()),
    }:
        raise ValueError("installed source registry differs from reviewed authority")
    prior = conn.execute(
        "SELECT view_definitions->>'field_versions' AS definition FROM mf_live_facts.configuration WHERE policy_hash=%s ORDER BY generation DESC LIMIT 1",
        (fingerprint(monthly_current.policy()),),
    ).fetchone()
    if (
        not prior
        or current.view_definition(conn, "mf_multi_facts.prior_field_versions")
        != prior["definition"]
    ):
        raise ValueError("retained pilot assertion graph differs")


def capture(conn):
    if not installed(conn):
        return None
    verify_acl(conn, principal=evidence.ROLES[1])
    result = {
        "functions": fingerprint(
            conn.execute(
                "SELECT pg_get_functiondef(oid) AS definition FROM pg_proc WHERE pronamespace='mf_multi_facts'::regnamespace ORDER BY proname"
            ).fetchall()
        )
    }
    for table, key in (
        ("configuration", "generation"),
        ("sources", "content_hash"),
        ("snapshots", "snapshot_id"),
        ("runs", "run_id"),
        ("transitions", "transition_id"),
    ):
        relation = (
            "(SELECT content_hash,source_url,byte_length FROM mf_multi_facts.sources)"
            if table == "sources"
            else "mf_multi_facts." + table
        )
        rows = conn.execute(
            "SELECT "
            + key
            + "::text AS key,encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS sha256 FROM "
            + relation
            + " t ORDER BY "
            + key
        ).fetchall()
        result[table] = {r["key"]: r["sha256"] for r in rows}
    raw = conn.execute(
        "SELECT has_column_privilege(current_user,'mf_multi_facts.sources','content','SELECT') AS allowed"
    ).fetchone()["allowed"]
    if (
        raw
        and conn.execute(
            "SELECT 1 FROM mf_multi_facts.sources WHERE encode(sha256(content),'hex')<>content_hash OR octet_length(content)<>byte_length LIMIT 1"
        ).fetchone()
    ):
        raise ValueError("registered original integrity differs")
    result["original_byte_integrity_checked"] = raw
    return result


def install(conn, root):
    from mfdataindia.review.preservation import capture_preservation, compare_preservation_structure
    from mfdataindia.review.publication_operations import capture_review, compare_review

    with conn.transaction():
        who = conn.execute(
            "SELECT session_user AS name,current_user=session_user AS direct"
        ).fetchone()
        if who["name"] not in ("postgres", "mfdata_admin") or not who["direct"]:
            raise ValueError("direct installation owner required")
        acquire_nav_data_operation_lock(conn)
        if installed(conn):
            verify_acl(conn, principal=evidence.ROLES[1])
            return {"status": "ALREADY_INSTALLED", "source_rows_changed": 0}
        if current.policy_document(conn) != monthly_current.policy():
            raise ValueError("accepted v2 pilot baseline required")
        before = capture_preservation(conn, current_facts_baseline=True, structure_only=True)
        prior_review = capture_review(conn)
        raw = (root / "sql/optional" / MIGRATION).read_text()
        if (
            hashlib.sha256(raw.encode()).hexdigest() != SCHEMA_SHA256
            or raw.count("\nBEGIN;\n") != 1
            or not raw.rstrip().endswith("COMMIT;")
        ):
            raise ValueError("registered migration differs from reviewed pin")
        conn.execute(raw.replace("\nBEGIN;\n", "\n", 1).rstrip()[: -len("COMMIT;")])
        conn.execute(
            "INSERT INTO mf_multi_facts.configuration VALUES(1,%s,%s,%s)",
            (
                fingerprint(multi_facts.registry()),
                Jsonb(multi_facts.registry()),
                fingerprint(multi_facts.policy()),
            ),
        )
        conn.execute(
            "CREATE VIEW mf_multi_facts.prior_field_versions AS "
            + current.view_definition(conn, "mf_live_facts.field_versions")
        )
        # The restored administrator's default grants also apply to new views.
        for row in conn.execute(
            "SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole"
        ).fetchall():
            conn.execute(
                sql.SQL("REVOKE ALL ON mf_multi_facts.prior_field_versions FROM {}").format(
                    sql.Identifier(row["rolname"])
                )
            )
        conn.execute("REVOKE ALL ON mf_multi_facts.prior_field_versions FROM PUBLIC")
        for role in evidence.ROLES:
            conn.execute(
                sql.SQL("GRANT SELECT ON mf_multi_facts.prior_field_versions TO {}").format(
                    sql.Identifier(role)
                )
            )
        for document in (multi_facts.policy(), multi_facts.current_policy()):
            ph = fingerprint(document)
            review._insert_exact(
                conn,
                "policies",
                "policy_hash",
                ph,
                document,
                {"policy_id": document["id"], "policy_version": document["version"]},
            )
        verify_acl(conn, principal=evidence.ROLES[1])
        after_review = capture_review(conn)
        added = {fingerprint(multi_facts.policy()), fingerprint(multi_facts.current_policy())}
        cleaned = {
            **after_review,
            "policies": {
                k: v
                for k, v in after_review["policies"].items()
                if k not in added - set(prior_review["policies"])
            },
        }
        compare_review(prior_review, cleaned)
        compare_preservation_structure(
            before, capture_preservation(conn, current_facts_baseline=True, structure_only=True)
        )
        return {
            "status": "INSTALLED",
            "source_rows_changed": 0,
            "registry_hash": fingerprint(multi_facts.registry()),
        }


def append_exact(conn, table, key, value, extra):
    document = dict(value)
    columns = {key: document[key], **extra, "document": Jsonb(document)}
    conn.execute(
        sql.SQL("INSERT INTO mf_multi_facts.{}({}) VALUES({}) ON CONFLICT({}) DO NOTHING").format(
            sql.Identifier(table),
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.SQL(",").join(sql.Placeholder() for _ in columns),
            sql.Identifier(key),
        ),
        list(columns.values()),
    )
    retained = conn.execute(
        sql.SQL("SELECT document FROM mf_multi_facts.{} WHERE {}=%s").format(
            sql.Identifier(table), sql.Identifier(key)
        ),
        (document[key],),
    ).fetchone()
    if retained["document"] != document:
        raise ValueError("immutable registered artifact replay differs")
    return document


def append_run(conn, body):
    doc = {**body, "run_id": fingerprint(body)}
    return append_exact(
        conn, "runs", "run_id", doc, {k: doc[k] for k in ("execution_id", "status", "observed_at")}
    )


def status(conn):
    verify_acl(conn, principal=evidence.ROLES[1])
    pilot = conn.execute(
        "SELECT document FROM mf_fact_refresh.runs ORDER BY observed_at DESC,run_id DESC LIMIT 1"
    ).fetchone()
    rows = conn.execute(
        "SELECT document FROM mf_multi_facts.runs ORDER BY observed_at DESC,run_id DESC LIMIT 10"
    ).fetchall()
    return {
        "format": "registered-facts-status-v1",
        "registry_hash": fingerprint(multi_facts.registry()),
        "sources": [
            {
                "key": "absl-large-cap",
                "latest_run": pilot["document"] if pilot else None,
                "discovery": "retained-official-sitecore-catalog",
            },
            {
                "key": "hdfc-flexi-cap",
                "latest_run": rows[0]["document"] if rows else None,
                "discovery": "reviewed-edition-only-no-current-month-claim",
            },
        ],
        "runs": [r["document"] for r in rows],
        "all_amc_data_complete": False,
        "source_rows_changed": 0,
    }


def get(conn, code):
    verify_acl(conn, principal=evidence.ROLES[1])
    identity = evidence.identity(conn, code)
    rows = conn.execute(
        "SELECT s.document,EXISTS(SELECT 1 FROM mf_multi_facts.runs r WHERE r.status<>'failed' AND r.document->'snapshot_ids' ? s.snapshot_id) AS published FROM mf_multi_facts.snapshots s WHERE s.amfi_scheme_code=%s ORDER BY s.disclosure_as_of DESC,s.snapshot_id LIMIT 101",
        (code,),
    ).fetchall()
    if len(rows) > 100:
        raise ValueError("registered history bound exceeded; never truncate silently")
    return {
        "format": "registered-fund-facts-v1",
        "code": code,
        "identity_hash": fingerprint(identity),
        "snapshots": [
            {
                "snapshot": r["document"],
                "published": r["published"],
                "current_identity_matches": r["document"]["identity"] == identity,
            }
            for r in rows
        ],
        "all_amc_data_complete": False,
        "originals_replaced": False,
        "adjusted_idcw_analytics_enabled": False,
    }
