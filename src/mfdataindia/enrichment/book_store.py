"""Owner-installed, least-privilege, append-only whole-AMC book evidence."""

import hashlib
from pathlib import Path

from psycopg import sql
from psycopg.types.json import Jsonb

from mfdataindia.enrichment import book_facts, book_sources, current_facts_store as current
from mfdataindia.enrichment import multi_facts, store as evidence
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_principal
from mfdataindia.store.locks import acquire_nav_data_operation_lock

MIGRATION = "038_amc_book_facts.sql"
SCHEMA_SHA256 = "62a526c54d1d069ed1f68e0a681e1110ede700249051ff4de57bf97675dd8928"
TABLES = {"configuration", "sources", "batches", "runs", "transitions"}
APPEND = {"sources", "batches", "runs"}


def installed(conn):
    return bool(conn.execute("SELECT to_regnamespace('mf_book_facts') AS name").fetchone()["name"])


def migration_source(root=None):
    root = root or (
        Path("/app")
        if (Path("/app/sql/optional") / MIGRATION).is_file()
        else Path(__file__).resolve().parents[3]
    )
    raw = (root / "sql/optional" / MIGRATION).read_text()
    if hashlib.sha256(raw.encode()).hexdigest() != SCHEMA_SHA256:
        raise ValueError("book migration differs from reviewed pin")
    return raw


def verify_acl(conn, *, principal=None):
    principal = principal or conn.execute("SELECT current_user AS name").fetchone()["name"]
    verify_principal(conn, "generator" if principal == evidence.ROLES[0] else "api", principal)
    ns = conn.execute(
        "SELECT nspowner,pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_book_facts'"
    ).fetchone()
    if not ns or ns["owner"] not in ("postgres", "mfdata_admin"):
        raise ValueError("book evidence owner differs")
    objects = conn.execute(
        "SELECT relname,relkind,relowner FROM pg_class WHERE relnamespace='mf_book_facts'::regnamespace AND relkind IN('r','p','v','m','S')"
    ).fetchall()
    if {r["relname"] for r in objects} != TABLES | {"prior_field_versions"} or any(
        r["relowner"] != ns["nspowner"]
        or r["relkind"] != ("v" if r["relname"] == "prior_field_versions" else "r")
        for r in objects
    ):
        raise ValueError("book relation inventory/ownership differs")
    funcs = conn.execute(
        "SELECT proname,prosecdef,proowner,proconfig FROM pg_proc WHERE pronamespace='mf_book_facts'::regnamespace"
    ).fetchall()
    if funcs != [
        {
            "proname": "seal_input",
            "prosecdef": True,
            "proowner": ns["nspowner"],
            "proconfig": ["search_path=pg_catalog"],
        }
    ]:
        raise ValueError("book executable boundary differs")
    body = conn.execute(
        "SELECT prosrc FROM pg_proc WHERE pronamespace='mf_book_facts'::regnamespace AND proname='seal_input'"
    ).fetchone()["prosrc"]
    if body != migration_source().split("AS $$", 1)[1].split("$$;", 1)[0]:
        raise ValueError("book executable body differs from reviewed pin")
    triggers = conn.execute(
        "SELECT c.relname,t.tgname,t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='mf_book_facts'::regnamespace AND NOT t.tgisinternal"
    ).fetchall()
    if {(r["relname"], r["tgname"]) for r in triggers} != {
        (t, t + "_immutable") for t in TABLES
    } | {(t, t + "_sealed") for t in APPEND} or any(r["tgenabled"] != "O" for r in triggers):
        raise ValueError("book sealed/immutable triggers differ")
    grants = conn.execute("""WITH acl AS (
        SELECT 'relation' AS kind,relname AS name,relowner AS owner,relacl AS acl FROM pg_class WHERE relnamespace='mf_book_facts'::regnamespace
        UNION ALL SELECT 'column',c.relname,c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='mf_book_facts'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
        UNION ALL SELECT 'schema',nspname,nspowner,nspacl FROM pg_namespace WHERE nspname='mf_book_facts'
        UNION ALL SELECT 'function',proname,proowner,COALESCE(proacl,acldefault('f'::"char",proowner)) FROM pg_proc WHERE pronamespace='mf_book_facts'::regnamespace
    ) SELECT kind,name,pg_get_userbyid(x.grantee) AS role,x.privilege_type FROM acl CROSS JOIN LATERAL aclexplode(acl.acl) x WHERE x.grantee<>owner""").fetchall()
    for g in grants:
        allowed = set()
        if g["role"] in evidence.ROLES:
            if g["kind"] == "schema":
                allowed = {"USAGE"}
            elif g["kind"] in ("relation", "column"):
                allowed = {"SELECT"}
                if g["role"] == evidence.ROLES[0] and g["name"] in APPEND:
                    allowed.add("INSERT")
        if g["privilege_type"] not in allowed:
            raise ValueError("book grants escape least privilege")
    for table in TABLES | {"prior_field_versions"}:
        p = conn.execute(
            "SELECT has_table_privilege(%s,%s,'INSERT') OR has_any_column_privilege(%s,%s,'INSERT') AS append,has_table_privilege(%s,%s,'UPDATE,DELETE,TRUNCATE') OR has_any_column_privilege(%s,%s,'UPDATE') AS rewrite",
            (principal, "mf_book_facts." + table) * 4,
        ).fetchone()
        if p != {"append": principal == evidence.ROLES[0] and table in APPEND, "rewrite": False}:
            raise ValueError("book effective mutation boundary differs")
    raw = conn.execute(
        "SELECT has_column_privilege(%s,'mf_book_facts.sources','content','SELECT') AS allowed,has_schema_privilege(%s,'mf_book_facts','CREATE') AS ddl",
        (principal, principal),
    ).fetchone()
    if raw != {"allowed": principal == evidence.ROLES[0], "ddl": False}:
        raise ValueError("book raw/DDL boundary differs")
    cfg = conn.execute(
        "SELECT document FROM mf_book_facts.configuration ORDER BY generation DESC LIMIT 1"
    ).fetchone()
    if not cfg or cfg["document"] != book_sources.configuration():
        raise ValueError("installed book authority differs from reviewed configuration")
    prior = conn.execute(
        "SELECT view_definitions->>'field_versions' AS definition FROM mf_live_facts.configuration WHERE policy_hash=%s ORDER BY generation DESC LIMIT 1",
        (fingerprint(multi_facts.current_policy()),),
    ).fetchone()
    if (
        not prior
        or current.view_definition(conn, "mf_book_facts.prior_field_versions")
        != prior["definition"]
    ):
        raise ValueError("retained v3 assertion graph differs")


def capture(conn):
    if not installed(conn):
        return None
    verify_acl(conn, principal=evidence.ROLES[1])
    result = {
        "functions": fingerprint(
            conn.execute(
                "SELECT pg_get_functiondef(oid) AS definition FROM pg_proc WHERE pronamespace='mf_book_facts'::regnamespace ORDER BY proname"
            ).fetchall()
        )
    }
    for table, key in (
        ("configuration", "generation"),
        ("sources", "source_key||':'||content_hash"),
        ("batches", "batch_id"),
        ("runs", "run_id"),
        ("transitions", "transition_id"),
    ):
        relation = (
            "(SELECT source_key,content_hash,source_url,media_type,byte_length FROM mf_book_facts.sources)"
            if table == "sources"
            else "mf_book_facts." + table
        )
        rows = conn.execute(
            "SELECT ("
            + key
            + ")::text AS key,encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS sha256 FROM "
            + relation
            + " t ORDER BY key"
        ).fetchall()
        result[table] = {r["key"]: r["sha256"] for r in rows}
    raw = conn.execute(
        "SELECT has_column_privilege(current_user,'mf_book_facts.sources','content','SELECT') AS allowed"
    ).fetchone()["allowed"]
    if (
        raw
        and conn.execute(
            "SELECT 1 FROM mf_book_facts.sources WHERE encode(sha256(content),'hex')<>content_hash OR octet_length(content)<>byte_length LIMIT 1"
        ).fetchone()
    ):
        raise ValueError("book original byte integrity differs")
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
            raise ValueError("direct book installation owner required")
        acquire_nav_data_operation_lock(conn)
        if installed(conn):
            verify_acl(conn, principal=evidence.ROLES[1])
            return {"status": "ALREADY_INSTALLED", "source_rows_changed": 0}
        if current.policy_document(conn) != multi_facts.current_policy():
            raise ValueError("accepted v3 registered baseline required")
        before = capture_preservation(conn, current_facts_baseline=True, structure_only=True)
        prior_review = capture_review(conn)
        raw = migration_source(root)
        if (
            hashlib.sha256(raw.encode()).hexdigest() != SCHEMA_SHA256
            or raw.count("\nBEGIN;\n") != 1
            or not raw.rstrip().endswith("COMMIT;")
        ):
            raise ValueError("book migration differs from reviewed pin")
        conn.execute(raw.replace("\nBEGIN;\n", "\n", 1).rstrip()[: -len("COMMIT;")])
        document = book_sources.configuration()
        conn.execute(
            "INSERT INTO mf_book_facts.configuration VALUES(1,%s,%s)",
            (fingerprint(document), Jsonb(document)),
        )
        conn.execute(
            "CREATE VIEW mf_book_facts.prior_field_versions AS "
            + current.view_definition(conn, "mf_live_facts.field_versions")
        )
        for row in conn.execute(
            "SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole"
        ).fetchall():
            conn.execute(
                sql.SQL("REVOKE ALL ON mf_book_facts.prior_field_versions FROM {}").format(
                    sql.Identifier(row["rolname"])
                )
            )
        conn.execute("REVOKE ALL ON mf_book_facts.prior_field_versions FROM PUBLIC")
        for role in evidence.ROLES:
            conn.execute(
                sql.SQL("GRANT SELECT ON mf_book_facts.prior_field_versions TO {}").format(
                    sql.Identifier(role)
                )
            )
        p = book_sources.current_policy()
        review._insert_exact(
            conn,
            "policies",
            "policy_hash",
            fingerprint(p),
            p,
            {"policy_id": p["id"], "policy_version": p["version"]},
        )
        verify_acl(conn, principal=evidence.ROLES[1])
        after_review = capture_review(conn)
        cleaned = {
            **after_review,
            "policies": {
                k: v
                for k, v in after_review["policies"].items()
                if k in prior_review["policies"] or k != fingerprint(p)
            },
        }
        compare_review(prior_review, cleaned)
        compare_preservation_structure(
            before, capture_preservation(conn, current_facts_baseline=True, structure_only=True)
        )
        return {
            "status": "INSTALLED",
            "source_rows_changed": 0,
            "configuration_hash": fingerprint(document),
        }


def population(conn, source_key):
    spec = book_sources.source(source_key)
    codes = conn.execute(
        "SELECT f.amfi_scheme_code FROM mf.funds f JOIN mf.amcs a USING(amc_id) WHERE a.amfi_amc_name=%s AND f.in_scope ORDER BY f.amfi_scheme_code",
        (spec["amc"],),
    ).fetchall()
    if not 1 <= len(codes) <= spec["maximum_current_variants"]:
        raise ValueError("bounded registered current AMC population required")
    return [evidence.identity(conn, row["amfi_scheme_code"]) for row in codes]


def append_exact(conn, table, key, doc, extra):
    if (table, key) not in {
        ("batches", "batch_id"),
        ("runs", "run_id"),
        ("transitions", "transition_id"),
    }:
        raise ValueError("registered immutable artifact required")
    cols = {key: doc[key], **extra, "document": Jsonb(doc)}
    conn.execute(
        sql.SQL("INSERT INTO mf_book_facts.{}({}) VALUES({}) ON CONFLICT({}) DO NOTHING").format(
            sql.Identifier(table),
            sql.SQL(",").join(map(sql.Identifier, cols)),
            sql.SQL(",").join(sql.Placeholder() for _ in cols),
            sql.Identifier(key),
        ),
        list(cols.values()),
    )
    retained = conn.execute(
        sql.SQL("SELECT document FROM mf_book_facts.{} WHERE {}=%s").format(
            sql.Identifier(table), sql.Identifier(key)
        ),
        (doc[key],),
    ).fetchone()
    if retained["document"] != doc:
        raise ValueError("immutable book artifact replay differs")
    return doc


def append_run(conn, body):
    doc = {**body, "run_id": fingerprint(body)}
    return append_exact(
        conn,
        "runs",
        "run_id",
        doc,
        {k: doc[k] for k in ("source_key", "execution_id", "status", "observed_at")},
    )


def status(conn):
    verify_acl(conn, principal=evidence.ROLES[1])
    sources = []
    for source in book_sources.registry()["sources"]:
        rows = conn.execute(
            "SELECT document FROM mf_book_facts.runs WHERE source_key=%s ORDER BY observed_at DESC,run_id DESC LIMIT 1",
            (source["key"],),
        ).fetchone()
        good = conn.execute(
            "SELECT document FROM mf_book_facts.runs WHERE source_key=%s AND status<>'failed' ORDER BY observed_at DESC,run_id DESC LIMIT 1",
            (source["key"],),
        ).fetchone()
        reports = []
        if good:
            for batch_id in good["document"]["batch_ids"]:
                batch = conn.execute(
                    "SELECT document FROM mf_book_facts.batches WHERE batch_id=%s", (batch_id,)
                ).fetchone()["document"]
                reports.append(book_facts.coverage(batch))
        live = population(conn, source["key"])
        sources.append(
            {
                "source_key": source["key"],
                "current_denominator": len(live),
                "current_population_hash": fingerprint(live),
                "latest_run": rows["document"] if rows else None,
                "latest_successful_run": good["document"] if good else None,
                "periods": reports,
                "population_matches_latest_success": bool(
                    good and good["document"]["population_hash"] == fingerprint(live)
                ),
            }
        )
    return {
        "format": "official-amc-book-status-v1",
        "configuration_hash": fingerprint(book_sources.configuration()),
        "sources": sources,
        "source_rows_changed": 0,
        "all_amc_data_complete": False,
        "adjusted_idcw_analytics_enabled": False,
    }


def fund(conn, code):
    verify_acl(conn, principal=evidence.ROLES[1])
    identity = evidence.identity(conn, code)
    rows = conn.execute(
        """SELECT b.batch_id,b.disclosure_as_of,x.value AS observation FROM mf_book_facts.batches b
        CROSS JOIN LATERAL jsonb_array_elements(b.document->'observations') x
        WHERE (x.value->'identity'->>'code')::integer=%s AND EXISTS(SELECT 1 FROM mf_book_facts.runs r WHERE r.status<>'failed' AND r.document->'batch_ids' ? b.batch_id)
        ORDER BY b.disclosure_as_of DESC,b.batch_id LIMIT 20""",
        (code,),
    ).fetchall()
    return {
        "format": "official-amc-book-fund-v1",
        "identity": identity,
        "observations": [
            {
                "batch_id": r["batch_id"],
                "disclosure_as_of": r["disclosure_as_of"].isoformat(),
                "current_identity_matches": r["observation"]["identity"] == identity,
                **r["observation"],
            }
            for r in rows
        ],
        "verification_status": "needs_verification",
        "adjusted_idcw_analytics_enabled": False,
    }
