"""RO-01 real migrated disposable CI PostgreSQL and actual authenticated APIs.

Synthetic source fixtures provide deterministic rows, not source certification.
No shared DEV, cloud, secret, provider, source HTTP or public acceptance proof.
"""

from importlib.util import module_from_spec, spec_from_loader
import os
from pathlib import Path
import subprocess

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest
from fastapi.testclient import TestClient

from mfdataindia.review import broad_reader_policy as planner
from mfdataindia.review import reader_access_profile as profiles
from mfdataindia.review.app import create_review_app
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout_database import install_and_reconcile
from mfdataindia.store.postgres import PostgresStore
from test_amc_book_facts import prepared, publish
from test_verified_review_auth import assertion, trust
from test_verified_review_rollout import manifest

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
BASE = "03f3932eb22218478f82e7985e1d0ba527f22cde"
PASSWORD = "verified-local-fixture-only"
ROLES = ("mfdata_admin", "mfdata_verified_generator", "mfdata_verified_reader",
         "mfdata_verified_admin", "mfdata_ingest")
RAW = ("mf_monthly_facts.sources", "mf_book_facts.sources",
       "mf_multi_facts.sources", "mf_fact_refresh.catalogs")
GUARDS = ("monthly_store", "book_store", "multi_store", "refresh_store")
ERRORS = ("monthly original-byte capability differs", "book raw/DDL boundary differs",
          "registered facts raw/DDL capabilities differ", "refresh raw catalog capability differs")


def connect(dsn, role="mfdata_admin", *, autocommit=True):
    return psycopg.connect(make_conninfo(dsn, user=role, password=PASSWORD),
                           autocommit=autocommit, row_factory=dict_row)


@pytest.fixture
def ro01_db(pg_dsn, monkeypatch, request):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    assert os.environ.get("GITHUB_ACTIONS") == "true", "RO01 requires disposable hosted CI"
    created, database_created = [], False
    with psycopg.connect(pg_dsn, autocommit=True, row_factory=dict_row) as admin:
        assert not admin.execute("SELECT 1 FROM pg_database WHERE datname='mfdataindia'").fetchone()
        assert not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (list(ROLES),)).fetchone()
        try:
            for role in ROLES:
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(PASSWORD)))
                created.append(role)
            admin.execute("CREATE DATABASE mfdataindia OWNER mfdata_admin")
            database_created = True
            dsn = make_conninfo(pg_dsn, dbname="mfdataindia", user="mfdata_admin", password=PASSWORD)
            with PostgresStore(dsn, use_copy=False) as store:
                store.apply_migrations(ROOT / "sql")
            with connect(dsn, autocommit=False) as conn:
                conn.execute("INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES('Verified fixture','VERIFIED FIXTURE')")
                for code, option in ((1, "GROWTH"), (2, "IDCW")):
                    conn.execute("INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type) VALUES(%s,1,%s,%s,'OPEN_ENDED','Equity','REGULAR',%s)",
                                 (code, f"Fund {code}", f"FUND {code}", option))
                    conn.execute("INSERT INTO mf.fund_variants(amfi_scheme_code,group_key,base_scheme_name) VALUES(%s,%s,%s)", (code, str(code), f"Fund {code}"))
                    conn.execute("INSERT INTO mf.fund_facts(amfi_scheme_code,source,aum,return_1year) VALUES(%s,'MANUAL',123.45,999)", (code,))
            with PostgresStore(dsn, use_copy=False) as store:
                store.apply_migrations(ROOT / "sql/optional", files=("023_verified_fund_review.sql",))
            # Establish the unchanged restricted base in this disposable fixture
            # only. The supported RO-01 install path must not call this reset.
            with psycopg.connect(make_conninfo(pg_dsn, dbname="mfdataindia"), row_factory=dict_row) as conn:
                conn.execute((ROOT / "scripts/sql/reconcile_verified_review_privileges.sql").read_text())
            if getattr(request, "param", None) == "core":
                yield dsn
                return
            identities, inputs, _ = prepared(dsn, monkeypatch)
            publish(dsn, identities, inputs)
            # Directly related sibling source guards are real installed modules,
            # not SQL doubles or fabricated reduced grant statements.
            from mfdataindia.enrichment import portfolio_operations, distribution_operations, idcw_operations
            from mfdataindia.analytics import operations as calendar_operations, rolling_operations, overlap_operations
            for operation in (calendar_operations, rolling_operations, portfolio_operations,
                              overlap_operations, distribution_operations, idcw_operations):
                with connect(dsn, autocommit=False) as conn:
                    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    operation.install(conn, ROOT)
            yield dsn
        finally:
            if database_created:
                admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='mfdataindia'")
                admin.execute("DROP DATABASE mfdataindia")
            for role in reversed(created):
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def baseline_guard(name):
    relative = f"src/mfdataindia/enrichment/{name}.py"
    raw = subprocess.check_output(["git", "show", BASE + ":" + relative], cwd=ROOT)
    module = module_from_spec(spec_from_loader("ro01_baseline_" + name, loader=None))
    module.__file__ = str(ROOT / relative)
    exec(compile(raw, relative, "exec"), module.__dict__)
    # Reproduce the original guard together with its original privilege helper,
    # not today's stricter selected-profile helper that rejects earlier.
    dependency = "src/mfdataindia/review/privileges.py"
    prior = module_from_spec(spec_from_loader("ro01_baseline_privileges", loader=None))
    prior_raw = subprocess.check_output(["git", "show", BASE + ":" + dependency], cwd=ROOT)
    exec(compile(prior_raw, dependency, "exec"), prior.__dict__)
    module.verify_principal = prior.verify_principal
    return module.verify_acl


def current_guard(name):
    from importlib import import_module
    return import_module("mfdataindia.enrichment." + name).verify_acl


def catalogue(dsn):
    with connect(dsn) as conn:
        return conn.execute(planner.STATE_SQL).fetchone()["value"]


def values(dsn, *, fixture_admin=False):
    result = {}
    with (psycopg.connect(dsn, autocommit=True, row_factory=dict_row) if fixture_admin else connect(dsn)) as conn:
        relations = conn.execute("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY(%s) AND c.relkind IN('r','p','m') ORDER BY n.nspname,c.relname", (list(planner.SCHEMAS),)).fetchall()
        for row in relations:
            rows = conn.execute(sql.SQL("SELECT to_jsonb(t)::text AS value FROM {}.{} t ORDER BY to_jsonb(t)::text").format(
                sql.Identifier(row["nspname"]), sql.Identifier(row["relname"]))).fetchall()
            result[row["nspname"] + "." + row["relname"]] = fingerprint(rows)
    return result


def adopt(dsn):
    # Exercise the actual production installation/planner composition in its
    # own existing transaction; no hand-written reduced grant population.
    return install_and_reconcile(dsn, ROOT, manifest(), {}, reader_profile=profiles.BROAD)


def test_native_base_raw_conflicts_and_real_broad_application_startup_and_reads(ro01_db, capsys):
    dsn = ro01_db
    before_values = values(dsn)
    with connect(dsn, planner.READER) as conn:
        for relation in RAW:
            assert not conn.execute("SELECT has_column_privilege(current_user,%s,'content','SELECT') AS allowed", (relation,)).fetchone()["allowed"]
        for name in GUARDS:
            current_guard(name)(conn)  # Restricted profile remains the default.
    adopt(dsn)
    with connect(dsn, planner.READER) as conn:
        profiles.verify_selection(conn, profiles.BROAD)
        for relation in RAW:
            assert conn.execute("SELECT has_column_privilege(current_user,%s,'content','SELECT') AS allowed", (relation,)).fetchone()["allowed"]
        for name, reason in zip(GUARDS, ERRORS):
            with pytest.raises(ValueError, match=reason):
                baseline_guard(name)(conn)
            current_guard(name)(conn, reader_profile=profiles.BROAD)
        with capsys.disabled():
            print("RO01 native base failure witness: all four original raw-content guards reject actual planned broad SELECT; current broad guards pass", flush=True)
    private, verifier = trust()
    flags = dict(calendar=True, rolling=True, current_facts=True, fact_verification=True,
                 enrichment=True, official_facts=True, portfolios=True, overlap=True,
                 distributions=True, idcw_catalog=True, monthly_facts=True,
                 fact_refresh=True, multi_amc=True, amc_books=True)
    headers = {"Authorization": "Bearer " + assertion(private, scope=["review:read"])}
    reader_dsn = make_conninfo(dsn, user=planner.READER, password=PASSWORD)
    with TestClient(create_review_app(reader_dsn, verifier=verifier,
                                     reader_profile=profiles.BROAD, **flags)) as client:
        paths = ("/api/enrichment/v4/funds/103173/facts", "/api/enrichment/v5/status",
                 "/api/enrichment/v8/funds/101763/facts", "/api/enrichment/v9/funds/100001/facts")
        for path in paths:
            assert client.get(path).status_code == 401
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
        payload = {"expected_version": 0, "expected_input_hash": "a" * 64,
                   "result_id": "b" * 64, "status": "reviewed",
                   "disposition": "consistent_with_evidence", "reason": "fixture",
                   "evidence": ["fixture://only"]}
        for scope in (["review:read"], ["review:read", "review:admin"]):
            denied = client.post("/api/review/v1/funds/1/reviews", json=payload,
                                 headers={"Authorization": "Bearer " + assertion(private, scope=scope),
                                          "Idempotency-Key": "ro01-denied-write", "X-Reader-Profile": profiles.RESTRICTED})
            assert denied.status_code == 403
    assert values(dsn) == before_values


def test_native_install_reconcile_is_atomic_idempotent_and_preserves_other_roles(ro01_db):
    dsn = ro01_db
    before = values(dsn)
    with connect(dsn) as conn:
        conn.execute("GRANT SELECT ON mf.fund_variants TO mfdata_ingest")
    first = adopt(dsn)
    assert first["source_values_preserved"]
    enrolled = catalogue(dsn)
    second = adopt(dsn)
    assert second["source_values_preserved"] and catalogue(dsn) == enrolled
    assert profiles.prepare(enrolled)["sql"] == []
    assert set(profiles.prepare(enrolled)["coverage"]["present_schemas"]) == set(planner.SCHEMAS)
    assert all(row["capabilities"][0] for row in enrolled["relations"] if row["relkind"] != "S")
    assert all(row["capabilities"][0] for row in enrolled["columns"] if row["capabilities"] is not None)
    assert values(dsn) == before
    with connect(dsn) as conn:
        assert conn.execute("SELECT has_table_privilege('mfdata_ingest','mf.fund_variants','SELECT') AS allowed").fetchone()["allowed"]
        # Selected-reader defaults are schema-local and owner-specific only.
        owner = conn.execute("SELECT oid FROM pg_roles WHERE rolname='mfdata_admin'").fetchone()["oid"]
        assert all(row["defaclrole"] == owner and row["defaclnamespace"] != 0
                   for row in enrolled["defaults"] if any(atom[1] == next(r["oid"] for r in enrolled["roles"] if r["rolname"] == planner.READER) for atom in row["acl"]))


def test_native_restricted_install_remains_restricted_and_refuses_silent_downgrade(ro01_db):
    dsn = ro01_db
    before = values(dsn)
    for _ in range(2):
        install_and_reconcile(dsn, ROOT, manifest(), {}, reader_profile=profiles.RESTRICTED)
    with connect(dsn, planner.READER) as conn:
        for name in GUARDS:
            current_guard(name)(conn)
    assert values(dsn) == before
    adopt(dsn)
    enrolled = catalogue(dsn)
    with pytest.raises(ValueError):
        install_and_reconcile(dsn, ROOT, manifest(), {}, reader_profile=profiles.RESTRICTED)
    assert catalogue(dsn) == enrolled and values(dsn) == before


def test_native_broad_does_not_mutate_or_elevate_and_rejects_missing_or_unsafe_grants(ro01_db):
    dsn = ro01_db
    adopt(dsn)
    enrolled, original_values = catalogue(dsn), values(dsn)
    with connect(dsn, planner.READER) as conn:
        for command in ("INSERT INTO mf.fund_facts(amfi_scheme_code,source) VALUES(999,'MANUAL')",
                        "UPDATE mf.fund_facts SET aum=0", "DELETE FROM mf.fund_facts",
                        "CREATE TABLE mf.ro01_unauthorized(value int)",
                        "CREATE SCHEMA ro01_unauthorized", "ALTER ROLE mfdata_verified_reader CREATEROLE"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(command)
    assert catalogue(dsn) == enrolled and values(dsn) == original_values
    with connect(dsn) as conn:
        conn.execute("REVOKE SELECT ON mf_monthly_facts.sources FROM mfdata_verified_reader")
    # PostgreSQL's table REVOKE also removes this reader's explicit column
    # SELECT atoms. Repair must match the complete production-planned state,
    # not fabricate/reinstate the pre-REVOKE column ACL representation.
    expected = profiles.prepare(catalogue(dsn))
    assert expected["sql"]
    with connect(dsn, planner.READER) as conn:
        with pytest.raises(ValueError, match="READER_PROFILE_READ_GRANTS_MISSING"):
            profiles.verify_selection(conn, profiles.BROAD)
    adopt(dsn)
    repaired = catalogue(dsn)
    assert repaired == expected["after"] and values(dsn) == original_values
    adopt(dsn)
    assert catalogue(dsn) == repaired and values(dsn) == original_values
    with connect(dsn, "mfdata_verified_admin") as conn:
        with pytest.raises(ValueError, match="READER_PROFILE_IDENTITY_MISMATCH"):
            profiles.verify_selection(conn, profiles.BROAD)
    with connect(dsn) as conn:
        conn.execute("GRANT UPDATE(aum) ON mf.fund_facts TO mfdata_verified_reader")
    unsafe = catalogue(dsn)
    with pytest.raises(ValueError, match="INCOMPLETE_READER_ACCESS_PROFILE"):
        adopt(dsn)
    assert catalogue(dsn) == unsafe


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_core_only_cannot_silently_label_broad_current_and_future_reads_restricted(ro01_db):
    dsn = ro01_db
    adopt(dsn)
    enrolled, before = catalogue(dsn), values(dsn)
    assert profiles.prepare(enrolled)["coverage"]["present_schemas"] == ["mf", "mf_review"]
    assert not any(row["nspname"] == "mf_monthly_facts" for row in enrolled["namespaces"])
    with pytest.raises(profiles.ReaderAccessError, match="READER_PROFILE_SELECTION_CONFLICT"):
        install_and_reconcile(dsn, ROOT, manifest(), {}, reader_profile=profiles.RESTRICTED)
    with connect(dsn, planner.READER) as conn:
        from mfdataindia.review.privileges import verify_runtime
        with pytest.raises(profiles.ReaderAccessError, match="READER_PROFILE_SELECTION_CONFLICT"):
            verify_runtime(conn, "api")
    assert catalogue(dsn) == enrolled and values(dsn) == before


@pytest.mark.parametrize("mutation", ["body", "configuration", "acl", "declaration"])
def test_native_restricted_runtime_and_install_reject_unreviewed_privileged_helper(ro01_db, pg_dsn, mutation):
    dsn = ro01_db
    from mfdataindia.review.privileges import verify_runtime
    with connect(dsn, planner.READER) as conn:
        verify_runtime(conn, "api")
    signature = "mf_fact_review.dependencies(integer)"
    with connect(dsn) as conn:
        if mutation == "body":
            from test_reader_function_attestation_postgres import create_statement
            statement = create_statement().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION").replace(
                "END $$;", "END /* RO01 synthetic body drift */ $$;")
            conn.execute(statement)
        elif mutation == "configuration":
            # Synthetic private-setting injection needs the existing disposable
            # administrator, not a new SET grant/elevation for the function owner.
            with psycopg.connect(make_conninfo(pg_dsn, dbname="mfdataindia"), autocommit=True) as fixture_admin:
                fixture_admin.execute("ALTER FUNCTION " + signature + " SET synthetic.private_config='fixture-only'")
        elif mutation == "acl":
            conn.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO PUBLIC")
        else:
            conn.execute("ALTER FUNCTION " + signature + " COST 101")
    before, records = catalogue(dsn), values(dsn)
    with connect(dsn, planner.READER) as conn:
        with pytest.raises(profiles.ReaderAccessError, match="UNREVIEWED_READER_FUNCTION"):
            verify_runtime(conn, "api")
    with pytest.raises(profiles.ReaderAccessError, match="UNREVIEWED_READER_FUNCTION"):
        install_and_reconcile(dsn, ROOT, manifest(), {}, reader_profile=profiles.RESTRICTED)
    assert catalogue(dsn) == before and values(dsn) == records


@pytest.mark.parametrize("mutation", ["volatility", "strictness", "cost"])
def test_native_restricted_callee_complete_declaration_is_attested(ro01_db, mutation):
    dsn = ro01_db
    signature = "mf_review.canonical_json(jsonb)"
    with connect(dsn) as conn:
        conn.execute("ALTER FUNCTION " + signature + " " + {
            "volatility": "VOLATILE", "strictness": "CALLED ON NULL INPUT", "cost": "COST 101"}[mutation])
    before, records = catalogue(dsn), values(dsn)
    from mfdataindia.review.privileges import verify_runtime
    with connect(dsn, planner.READER) as conn:
        with pytest.raises(profiles.ReaderAccessError, match="UNREVIEWED_READER_FUNCTION"):
            verify_runtime(conn, "api")
    with pytest.raises(profiles.ReaderAccessError, match="UNREVIEWED_READER_FUNCTION"):
        install_and_reconcile(dsn, ROOT, manifest(), {}, reader_profile=profiles.RESTRICTED)
    assert catalogue(dsn) == before and values(dsn) == records


def test_native_historical_independent_owners_require_actual_callee_execution(ro01_db, pg_dsn):
    dsn = ro01_db
    from mfdataindia.review.privileges import verify_runtime
    from mfdataindia.enrichment import fact_review_store
    admin_dsn = make_conninfo(pg_dsn, dbname="mfdataindia")
    with psycopg.connect(admin_dsn, autocommit=True, row_factory=dict_row) as admin:
        before = admin.execute("SELECT to_jsonb(f)::text AS value FROM mf.funds f ORDER BY amfi_scheme_code").fetchall()
        admin.execute("ALTER SCHEMA mf_fact_review OWNER TO postgres")
        admin.execute("ALTER TABLE mf_fact_review.events OWNER TO postgres")
        for signature in ("dependencies(integer)", "record_review(integer,text,text,text,jsonb)"):
            admin.execute("ALTER FUNCTION mf_fact_review." + signature + " OWNER TO postgres")
        with connect(dsn, planner.READER) as conn:
            verify_runtime(conn, "api")
            fact_review_store.verify_acl(conn)
            document, checksum = fact_review_store.dependencies(conn, 1)
            assert set(document) == {"format", "code", "identity", "policy_hash", "fields"}
            assert document["format"] == "official-facts-review-dependencies-v1"
            assert document["code"] == document["identity"]["code"] == 1
            assert isinstance(document["fields"], dict) and len(checksum) == 64
        original_catalogue = catalogue(dsn)
        original_values = values(admin_dsn, fixture_admin=True)
        # Existing supported historical restricted owner path, not an elevated
        # mfdata_admin or a new grant to let it read postgres-owned private rows.
        result = install_and_reconcile(admin_dsn, ROOT, manifest(), {}, reader_profile=profiles.RESTRICTED)
        assert result["source_values_preserved"]
        assert catalogue(dsn) == original_catalogue
        assert values(admin_dsn, fixture_admin=True) == original_values
        for schema in ("mf_review", "mf_fact_review"):
            original = "mfdata_admin" if schema == "mf_review" else "postgres"
            admin.execute(sql.SQL("ALTER SCHEMA {} OWNER TO mfdata_ingest").format(sql.Identifier(schema)))
            with connect(dsn, planner.READER) as conn:
                with pytest.raises(profiles.ReaderAccessError, match="UNREVIEWED_READER_FUNCTION"):
                    verify_runtime(conn, "api")
            admin.execute(sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(sql.Identifier(schema), sql.Identifier(original)))
        # Reverse owner combination is not accepted by assumption: the helper
        # owner cannot execute this owner-only callee in the disposable fixture.
        admin.execute("ALTER SCHEMA mf_fact_review OWNER TO mfdata_admin")
        admin.execute("ALTER FUNCTION mf_fact_review.dependencies(integer) OWNER TO mfdata_admin")
        admin.execute("ALTER SCHEMA mf_review OWNER TO postgres")
        admin.execute("ALTER FUNCTION mf_review.canonical_json(jsonb) OWNER TO postgres")
        assert not admin.execute("SELECT has_function_privilege('mfdata_admin','mf_review.canonical_json(jsonb)','EXECUTE') AS allowed").fetchone()["allowed"]
        with connect(dsn, planner.READER) as conn:
            with pytest.raises(profiles.ReaderAccessError, match="UNREVIEWED_READER_FUNCTION"):
                verify_runtime(conn, "api")
        assert admin.execute("SELECT to_jsonb(f)::text AS value FROM mf.funds f ORDER BY amfi_scheme_code").fetchall() == before


def test_native_restricted_missing_helper_schema_usage_rejects_before_reconciliation(ro01_db):
    dsn = ro01_db
    from mfdataindia.review.privileges import verify_runtime
    with connect(dsn) as conn:
        conn.execute("REVOKE USAGE ON SCHEMA mf_fact_review FROM mfdata_verified_reader")
    before, records = catalogue(dsn), values(dsn)
    with connect(dsn, planner.READER) as conn:
        assert not conn.execute("SELECT has_schema_privilege(current_user,'mf_fact_review','USAGE') AS allowed").fetchone()["allowed"]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT mf_fact_review.dependencies(1)")
        with pytest.raises(profiles.ReaderAccessError, match="UNREVIEWED_READER_FUNCTION"):
            verify_runtime(conn, "api")
    with pytest.raises(profiles.ReaderAccessError, match="UNREVIEWED_READER_FUNCTION"):
        install_and_reconcile(dsn, ROOT, manifest(), {}, reader_profile=profiles.RESTRICTED)
    assert catalogue(dsn) == before and values(dsn) == records
