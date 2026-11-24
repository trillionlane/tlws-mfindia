"""Isolated registry-9 store boundary; legacy book store bytes remain sealed."""

import hashlib
import json
from pathlib import Path

from mfdataindia.enrichment import book_facts, book_runtime, book_store as legacy
from mfdataindia.enrichment import current_facts_store as current
from mfdataindia.enrichment import multi_facts, sbi_book_v2 as sbi_book
from mfdataindia.enrichment import sbi_book_sources_v2 as sources
from mfdataindia.enrichment import store as evidence
from mfdataindia.review import reader_access_profile as profiles
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_principal, verify_runtime
from mfdataindia.store.locks import acquire_nav_data_operation_lock

MIGRATIONS = {
    9: (
        "050_sbi_document_as_of_evidence.sql",
        "ba2e7fb6073136a57bdfced59c30d5705d2e61b80e9d69ef338a346d9a52b422",
    )
}
TABLES = legacy.TABLES
APPEND = legacy.APPEND


def installed(conn):
    return legacy.installed(conn)


def migration_source(root=None, *, version=9):
    if version in legacy.MIGRATIONS:
        return legacy.migration_source(root, version=version)
    if type(version) is not int or version != 9:
        raise ValueError("isolated SBI v2 migration version required")
    filename, digest = MIGRATIONS[version]
    root = root or (
        Path("/app")
        if (Path("/app/sql/optional") / filename).is_file()
        else Path(__file__).resolve().parents[3]
    )
    raw = (root / "sql/optional" / filename).read_text()
    if hashlib.sha256(raw.encode()).hexdigest() != digest:
        raise ValueError("isolated SBI v2 migration differs from reviewed pin")
    return raw


def installed_configuration(conn):
    row = conn.execute(
        "SELECT generation,configuration_hash,document FROM mf_book_facts.configuration "
        "ORDER BY generation DESC LIMIT 1"
    ).fetchone()
    version = row["document"].get("registry", {}).get("version") if row else None
    if version == 9:
        expected = sources.configuration()
    elif type(version) is int and version in legacy.book_sources.REGISTRIES:
        expected = legacy.book_sources.configuration(version)
    else:
        raise ValueError("installed book authority differs from isolated reviewed configuration")
    if row["document"] != expected or row["configuration_hash"] != fingerprint(expected):
        raise ValueError("installed book authority differs from isolated reviewed configuration")
    return row


def verify_acl(conn, *, principal=None, reader_profile=profiles.RESTRICTED):
    principal = principal or conn.execute("SELECT current_user AS name").fetchone()["name"]
    verify_principal(
        conn,
        "generator" if principal == evidence.ROLES[0] else "api",
        principal,
        **profiles.guard_options(reader_profile),
    )
    ns = conn.execute(
        "SELECT nspowner,pg_get_userbyid(nspowner) AS owner FROM pg_namespace "
        "WHERE nspname='mf_book_facts'"
    ).fetchone()
    if not ns or ns["owner"] not in ("postgres", "mfdata_admin"):
        raise ValueError("book evidence owner differs")
    objects = conn.execute(
        "SELECT relname,relkind,relowner FROM pg_class "
        "WHERE relnamespace='mf_book_facts'::regnamespace "
        "AND relkind IN('r','p','v','m','S')"
    ).fetchall()
    if {row["relname"] for row in objects} != TABLES | {"prior_field_versions"} or any(
        row["relowner"] != ns["nspowner"]
        or row["relkind"] != ("v" if row["relname"] == "prior_field_versions" else "r")
        for row in objects
    ):
        raise ValueError("book relation inventory/ownership differs")
    funcs = conn.execute(
        "SELECT proname,prosecdef,proowner,proconfig FROM pg_proc "
        "WHERE pronamespace='mf_book_facts'::regnamespace"
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
    cfg = installed_configuration(conn)
    body = conn.execute(
        "SELECT prosrc FROM pg_proc WHERE pronamespace='mf_book_facts'::regnamespace "
        "AND proname='seal_input'"
    ).fetchone()["prosrc"]
    if body != migration_source(
        version=cfg["document"]["registry"]["version"]
    ).split("AS $$", 1)[1].split("$$;", 1)[0]:
        raise ValueError("book executable body differs from reviewed pin")
    triggers = conn.execute(
        "SELECT c.relname,t.tgname,t.tgenabled FROM pg_trigger t "
        "JOIN pg_class c ON c.oid=t.tgrelid "
        "WHERE c.relnamespace='mf_book_facts'::regnamespace AND NOT t.tgisinternal"
    ).fetchall()
    if {(row["relname"], row["tgname"]) for row in triggers} != {
        (table, table + "_immutable") for table in TABLES
    } | {(table, table + "_sealed") for table in APPEND} or any(
        row["tgenabled"] != "O" for row in triggers
    ):
        raise ValueError("book sealed/immutable triggers differ")
    grants = conn.execute(
        """WITH acl AS (
        SELECT 'relation' AS kind,relname AS name,relowner AS owner,relacl AS acl FROM pg_class WHERE relnamespace='mf_book_facts'::regnamespace
        UNION ALL SELECT 'column',c.relname,c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='mf_book_facts'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
        UNION ALL SELECT 'schema',nspname,nspowner,nspacl FROM pg_namespace WHERE nspname='mf_book_facts'
        UNION ALL SELECT 'function',proname,proowner,COALESCE(proacl,acldefault('f'::"char",proowner)) FROM pg_proc WHERE pronamespace='mf_book_facts'::regnamespace
    ) SELECT kind,name,pg_get_userbyid(x.grantee) AS role,x.privilege_type FROM acl CROSS JOIN LATERAL aclexplode(acl.acl) x WHERE x.grantee<>owner"""
    ).fetchall()
    for grant in grants:
        allowed = set()
        if grant["role"] in evidence.ROLES:
            if grant["kind"] == "schema":
                allowed = {"USAGE"}
            elif grant["kind"] in ("relation", "column"):
                allowed = {"SELECT"}
                if grant["role"] == evidence.ROLES[0] and grant["name"] in APPEND:
                    allowed.add("INSERT")
        if grant["privilege_type"] not in allowed:
            raise ValueError("book grants escape least privilege")
    for table in TABLES | {"prior_field_versions"}:
        privileges = conn.execute(
            "SELECT has_table_privilege(%s,%s,'INSERT') OR "
            "has_any_column_privilege(%s,%s,'INSERT') AS append,"
            "has_table_privilege(%s,%s,'UPDATE,DELETE,TRUNCATE') OR "
            "has_any_column_privilege(%s,%s,'UPDATE') AS rewrite",
            (principal, "mf_book_facts." + table) * 4,
        ).fetchone()
        if privileges != {
            "append": principal == evidence.ROLES[0] and table in APPEND,
            "rewrite": False,
        }:
            raise ValueError("book effective mutation boundary differs")
    raw = conn.execute(
        "SELECT has_column_privilege(%s,'mf_book_facts.sources','content','SELECT') AS allowed,"
        "has_schema_privilege(%s,'mf_book_facts','CREATE') AS ddl",
        (principal, principal),
    ).fetchone()
    if raw != {
        "allowed": profiles.source_read_expected(principal, reader_profile),
        "ddl": False,
    }:
        raise ValueError("book raw/DDL boundary differs")
    prior = conn.execute(
        "SELECT view_definitions->>'field_versions' AS definition "
        "FROM mf_live_facts.configuration WHERE policy_hash=%s "
        "ORDER BY generation DESC LIMIT 1",
        (fingerprint(multi_facts.current_policy()),),
    ).fetchone()
    if not prior or current.view_definition(
        conn, "mf_book_facts.prior_field_versions"
    ) != prior["definition"]:
        raise ValueError("retained v3 assertion graph differs")


def capture(conn, *, reader_profile=profiles.RESTRICTED):
    if not installed(conn):
        return None
    verify_acl(
        conn,
        principal=evidence.ROLES[1],
        **profiles.guard_options(reader_profile),
    )
    result = {
        "functions": fingerprint(
            conn.execute(
                "SELECT pg_get_functiondef(oid) AS definition FROM pg_proc "
                "WHERE pronamespace='mf_book_facts'::regnamespace ORDER BY proname"
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
            "(SELECT source_key,content_hash,source_url,media_type,byte_length "
            "FROM mf_book_facts.sources)"
            if table == "sources"
            else "mf_book_facts." + table
        )
        rows = conn.execute(
            "SELECT ("
            + key
            + ")::text AS key,encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') "
            "AS sha256 FROM "
            + relation
            + " t ORDER BY key"
        ).fetchall()
        result[table] = {row["key"]: row["sha256"] for row in rows}
    raw = conn.execute(
        "SELECT has_column_privilege(current_user,'mf_book_facts.sources','content','SELECT') "
        "AS allowed"
    ).fetchone()["allowed"]
    if raw and conn.execute(
        "SELECT 1 FROM mf_book_facts.sources WHERE "
        "encode(sha256(content),'hex')<>content_hash OR "
        "octet_length(content)<>byte_length LIMIT 1"
    ).fetchone():
        raise ValueError("book original byte integrity differs")
    result["original_byte_integrity_checked"] = raw
    return result


def population(conn, source_key):
    if source_key != sbi_book.KEY:
        raise ValueError("isolated SBI v2 population required")
    spec = sources.source(source_key)
    codes = conn.execute(
        "SELECT f.amfi_scheme_code FROM mf.funds f JOIN mf.amcs a USING(amc_id) "
        "WHERE a.amfi_amc_name=%s AND f.in_scope ORDER BY f.amfi_scheme_code",
        (spec["amc"],),
    ).fetchall()
    if not 1 <= len(codes) <= spec["maximum_current_variants"]:
        raise ValueError("bounded registered current AMC population required")
    return [evidence.identity(conn, row["amfi_scheme_code"]) for row in codes]


def append_exact(conn, table, key, doc, extra):
    return legacy.append_exact(conn, table, key, doc, extra)


def append_run(conn, body):
    return legacy.append_run(conn, body)


def receipt(conn, source_key, execution):
    row = conn.execute(
        "SELECT document FROM mf_book_facts.runs "
        "WHERE source_key=%s AND execution_id=%s",
        (source_key, execution),
    ).fetchone()
    return row["document"] if row else None


def publish(
    conn,
    key,
    execution,
    observed,
    identities,
    batches,
    originals,
    archives,
    *,
    availability=None,
):
    if key != sbi_book.KEY:
        raise ValueError("isolated SBI v2 publication required")
    verify_runtime(conn, "generator")
    verify_acl(conn)
    acquire_nav_data_operation_lock(conn)
    previous = receipt(conn, key, execution)
    if previous:
        return previous
    if population(conn, key) != identities:
        raise ValueError(
            "current population changed during extraction; preserve last good publication"
        )
    catalog_hashes = []
    for original in originals:
        digest = book_runtime.retain_source(conn, key, original)
        if (
            original["media_type"] in ("text/html", "application/json")
            and digest not in catalog_hashes
        ):
            catalog_hashes.append(digest)
    for batch in batches:
        if batch["population_hash"] != fingerprint(identities):
            raise ValueError("compiled complete population differs")
        append_exact(
            conn,
            "batches",
            "batch_id",
            batch,
            {
                field: batch[field]
                for field in (
                    "source_key",
                    "content_hash",
                    "identity_content_hash",
                    "disclosure_as_of",
                )
            },
        )
    batch_ids = [
        batch["batch_id"]
        for batch in sorted(
            batches,
            key=lambda item: (item["disclosure_as_of"], item["batch_id"]),
            reverse=True,
        )
    ]
    existing = conn.execute(
        "SELECT 1 FROM mf_book_facts.runs WHERE source_key=%s AND status<>'failed' "
        "AND document->'batch_ids'=%s::jsonb LIMIT 1",
        (key, json.dumps(batch_ids)),
    ).fetchone()
    dataset = conn.execute(
        "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
    ).fetchone()
    return append_run(
        conn,
        {
            "format": "official-amc-book-run-v1",
            "source_key": key,
            "execution_id": execution,
            "observed_at": observed.isoformat(),
            "status": "unchanged" if existing else "updated",
            "specification_hash": fingerprint(sources.specification(key)),
            "population_hash": fingerprint(identities),
            "dataset_version": dataset["dataset_version"],
            "batch_ids": batch_ids,
            "catalog_hashes": catalog_hashes,
            "archives": archives,
            "coverage": [book_facts.coverage(batch) for batch in batches],
            "source_rows_changed": 0,
            "all_amc_data_complete": False,
            "verification_status": "needs_verification",
            "adjusted_idcw_analytics_enabled": False,
            **({"availability": availability} if availability is not None else {}),
        },
    )
