"""Append-only source refresh receipts and short owner-only projection transitions."""

import hashlib
from pathlib import Path

from psycopg.types.json import Jsonb

from mfdataindia.enrichment import monthly_current, refresh_policy
from mfdataindia.enrichment import store as evidence
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_principal
from mfdataindia.review.preservation import capture_preservation, compare_preservation_structure
from mfdataindia.review.publication_operations import capture_review
from mfdataindia.store.locks import acquire_nav_data_operation_lock

MIGRATION = "035_official_fact_refresh.sql"
SCHEMA_SHA256 = "2bfb9a075d7ea42f6d3a074d37e44c98131e9726cfa29f8d4ef563030a58bb6c"
PROJECTION = "monthly_current_projection.sql.inc"
PROJECTION_SHA256 = "94069720889ef5893073f02f2cebcf7ac4666f9fd510386fff3d051079967d2f"
TABLES = {"catalogs", "runs", "publications", "transitions"}


def verify_acl(conn, *, principal=None):
    principal = principal or conn.execute("SELECT current_user AS name").fetchone()["name"]
    if principal not in evidence.ROLES:
        raise ValueError("dedicated source refresh principal required")
    verify_principal(conn, "generator" if principal == evidence.ROLES[0] else "api", principal)
    ns = conn.execute(
        "SELECT nspowner,pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_fact_refresh'"
    ).fetchone()
    if not ns or ns["owner"] not in ("postgres", "mfdata_admin"):
        raise ValueError("refresh namespace owner differs")
    objects = conn.execute(
        "SELECT relname,relkind,relowner FROM pg_class WHERE relnamespace='mf_fact_refresh'::regnamespace AND relkind IN('r','p','m','v','S')"
    ).fetchall()
    if {r["relname"] for r in objects} != TABLES or any(
        r["relkind"] != "r" or r["relowner"] != ns["nspowner"] for r in objects
    ):
        raise ValueError("refresh relation inventory differs")
    expected = {
        "catalogs": {"refresh_catalogs_immutable", "refresh_catalog_sealed"},
        "runs": {"refresh_runs_immutable", "refresh_run_publication", "refresh_run_sealed"},
        "publications": {"refresh_publications_immutable"},
        "transitions": {"refresh_transitions_immutable"},
    }
    for table in sorted(TABLES):
        rows = conn.execute(
            "SELECT tgname,tgenabled FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal",
            ("mf_fact_refresh." + table,),
        ).fetchall()
        if {r["tgname"] for r in rows} != expected[table] or any(
            r["tgenabled"] != "O" for r in rows
        ):
            raise ValueError("refresh seal/immutability trigger differs")
        values = conn.execute(
            "SELECT has_table_privilege(%s,%s,'INSERT') OR has_any_column_privilege(%s,%s,'INSERT') AS append,has_table_privilege(%s,%s,'UPDATE,DELETE,TRUNCATE') OR has_any_column_privilege(%s,%s,'UPDATE') AS rewrite",
            (principal, "mf_fact_refresh." + table) * 4,
        ).fetchone()
        if values != {
            "append": principal == evidence.ROLES[0] and table in ("catalogs", "runs"),
            "rewrite": False,
        }:
            raise ValueError("refresh effective DML capabilities differ")
    raw = conn.execute(
        "SELECT has_column_privilege(%s,'mf_fact_refresh.catalogs','content','SELECT') AS allowed",
        (principal,),
    ).fetchone()["allowed"]
    if raw != (principal == evidence.ROLES[0]):
        raise ValueError("refresh raw catalog capability differs")
    if conn.execute(
        "SELECT has_schema_privilege(%s,'mf_fact_refresh','CREATE') AS allowed", (principal,)
    ).fetchone()["allowed"]:
        raise ValueError("refresh runtime DDL is forbidden")
    functions = conn.execute(
        "SELECT proname,prosecdef,proowner,proconfig FROM pg_proc WHERE pronamespace='mf_fact_refresh'::regnamespace ORDER BY proname"
    ).fetchall()
    if functions != [
        {
            "proname": name,
            "prosecdef": True,
            "proowner": ns["nspowner"],
            "proconfig": ["search_path=pg_catalog"],
        }
        for name in ("publish_run", "seal_input")
    ]:
        raise ValueError("refresh function owner/inventory differs")
    grants = conn.execute("""WITH objects AS (
      SELECT relowner AS owner,relacl AS acl FROM pg_class WHERE relnamespace='mf_fact_refresh'::regnamespace
      UNION ALL SELECT c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='mf_fact_refresh'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
      UNION ALL SELECT nspowner,nspacl FROM pg_namespace WHERE nspname='mf_fact_refresh'
      UNION ALL SELECT proowner,COALESCE(proacl,acldefault('f'::\"char\",proowner)) FROM pg_proc WHERE pronamespace='mf_fact_refresh'::regnamespace
    ) SELECT pg_get_userbyid(x.grantee) AS principal,x.privilege_type FROM objects CROSS JOIN LATERAL aclexplode(acl) x WHERE x.grantee<>owner""").fetchall()
    if any(
        r["principal"] not in evidence.ROLES
        or r["privilege_type"]
        not in (
            {"USAGE", "SELECT", "INSERT"}
            if r["principal"] == evidence.ROLES[0]
            else {"USAGE", "SELECT"}
        )
        for r in grants
    ):
        raise ValueError("refresh grants escape the declared role boundary")
    for policy in (monthly_current.policy(), refresh_policy.policy()):
        row = conn.execute(
            "SELECT document FROM mf_review.policies WHERE policy_hash=%s", (fingerprint(policy),)
        ).fetchone()
        if not row or row["document"] != policy:
            raise ValueError("refresh/current policy missing or differs")


def capture(conn):
    if not conn.execute("SELECT to_regnamespace('mf_fact_refresh') AS ns").fetchone()["ns"]:
        return None
    verify_acl(conn, principal=evidence.ROLES[1])
    conn.execute("SET LOCAL TimeZone='UTC'")
    result = {}
    for table, key in (
        ("catalogs", "catalog_id"),
        ("runs", "run_id"),
        ("publications", "publication_id"),
        ("transitions", "transition_id"),
    ):
        relation = (
            "(SELECT catalog_id,source_url,media_type,content_hash FROM mf_fact_refresh.catalogs)"
            if table == "catalogs"
            else "mf_fact_refresh." + table
        )
        rows = conn.execute(
            "SELECT "
            + key
            + ",encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS sha256 FROM "
            + relation
            + " t ORDER BY "
            + key
        ).fetchall()
        result[table] = {
            "rows": len(rows),
            "sha256": fingerprint(rows),
            "row_hashes": {r[key]: r["sha256"] for r in rows},
        }
    result["function_hash"] = fingerprint(
        conn.execute(
            "SELECT pg_get_functiondef(p.oid) AS definition FROM pg_proc p WHERE pronamespace='mf_fact_refresh'::regnamespace ORDER BY proname"
        ).fetchall()
    )
    raw = conn.execute(
        "SELECT has_column_privilege(current_user,'mf_fact_refresh.catalogs','content','SELECT') AS allowed"
    ).fetchone()["allowed"]
    if (
        raw
        and conn.execute(
            "SELECT 1 FROM mf_fact_refresh.catalogs WHERE encode(sha256(content),'hex')<>content_hash LIMIT 1"
        ).fetchone()
    ):
        raise ValueError("retained official catalog bytes differ")
    result["original_catalog_integrity_checked"] = raw
    return result


def install(conn, root: Path):
    with conn.transaction():
        who = conn.execute(
            "SELECT session_user AS name,current_user=session_user AS direct"
        ).fetchone()
        if not who["direct"] or who["name"] not in ("postgres", "mfdata_admin"):
            raise ValueError("direct refresh migration owner required")
        if (
            conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            != "repeatable read"
        ):
            raise ValueError("consistent refresh installation required")
        acquire_nav_data_operation_lock(conn)
        if capture(conn) is not None:
            return {"status": "ALREADY_INSTALLED", "source_rows_changed": 0}
        before = capture_preservation(conn, current_facts_baseline=True, structure_only=True)
        prior = capture_review(conn)
        added = set()
        for policy in (monthly_current.policy(), refresh_policy.policy()):
            ph = fingerprint(policy)
            review._insert_exact(
                conn,
                "policies",
                "policy_hash",
                ph,
                policy,
                {"policy_id": policy["id"], "policy_version": policy["version"]},
            )
            added.add(ph)
        ddl = (root / "sql/optional" / MIGRATION).read_text()
        if (
            hashlib.sha256(ddl.encode()).hexdigest() != SCHEMA_SHA256
            or ddl.count("\nBEGIN;\n") != 1
            or not ddl.rstrip().endswith("COMMIT;")
        ):
            raise ValueError("refresh migration differs from reviewed pin")
        conn.execute(ddl.replace("\nBEGIN;\n", "\n", 1).rstrip()[: -len("COMMIT;")])
        for role in evidence.ROLES:
            verify_acl(conn, principal=role)
        compare_preservation_structure(
            before, capture_preservation(conn, current_facts_baseline=True, structure_only=True)
        )
        after = capture_review(conn)
        for table, rows in prior.items():
            if any(after[table].get(key) != value for key, value in rows.items()):
                raise ValueError("prior review artifact changed")
            if table == "policies":
                if set(after[table]) - set(rows) != added - set(rows):
                    raise ValueError("undeclared refresh policy addition")
            elif after[table] != rows:
                raise ValueError("undeclared review artifact addition")
        return {
            "status": "INSTALLED",
            "source_rows_changed": 0,
            "source_structure_preserved": True,
            "whole_source_preservation_requires_post_installation_certification": True,
        }


def append_catalog(conn, url, media_type, content):
    if (
        media_type not in ("text/html", "application/json")
        or not isinstance(content, bytes)
        or not 0 < len(content) <= 1048576
    ):
        raise ValueError("bounded original catalog required")
    body = {
        "source_url": url,
        "media_type": media_type,
        "content_hash": hashlib.sha256(content).hexdigest(),
    }
    key = fingerprint(body)
    conn.execute(
        "INSERT INTO mf_fact_refresh.catalogs VALUES(%s,%s,%s,%s,%s) ON CONFLICT(catalog_id) DO NOTHING",
        (key, url, media_type, body["content_hash"], content),
    )
    row = conn.execute(
        "SELECT source_url,media_type,content_hash,content FROM mf_fact_refresh.catalogs WHERE catalog_id=%s",
        (key,),
    ).fetchone()
    if {k: row[k] for k in body} != body or bytes(row["content"]) != content:
        raise review.Conflict("retained catalog differs; never overwrite")
    return key


def append_run(conn, body):
    # Runtime callers use the same publication lock; the DB trigger also
    # enforces it so direct INSERT cannot bypass complete-cohort publication.
    acquire_nav_data_operation_lock(conn)
    record = {**body, "run_id": fingerprint(body)}
    conn.execute(
        "INSERT INTO mf_fact_refresh.runs VALUES(%s,%s,%s,%s,%s) ON CONFLICT(execution_id) DO NOTHING",
        (
            record["run_id"],
            record["execution_id"],
            record["observed_at"],
            record["status"],
            Jsonb(record),
        ),
    )
    row = conn.execute(
        "SELECT document FROM mf_fact_refresh.runs WHERE execution_id=%s", (record["execution_id"],)
    ).fetchone()
    if not row or row["document"] != record:
        raise review.Conflict("retained refresh execution differs")
    return record


def status(conn):
    verify_acl(conn)
    rows = conn.execute(
        "SELECT document FROM mf_fact_refresh.runs ORDER BY observed_at DESC,run_id DESC LIMIT 10"
    ).fetchall()
    successful = conn.execute(
        "SELECT document FROM mf_fact_refresh.runs WHERE status<>'failed' "
        "AND document->>'mode'='scheduled_refresh' ORDER BY observed_at DESC,run_id DESC LIMIT 1"
    ).fetchone()
    return {
        "format": "official-facts-refresh-status-v1",
        "runs": [r["document"] for r in rows],
        "last_run": rows[0]["document"] if rows else None,
        "last_successful_source_check": successful["document"] if successful else None,
        "all_amc_coverage": False,
        "human_verification": "separate_from_source_refresh",
        "nav_dataset_version_is_facts_revision": False,
    }
