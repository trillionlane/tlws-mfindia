"""Exact production catalogue SQL in fresh disposable CI PG18 only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.pq import TransactionStatus
import pytest

from mfdataindia.review import broad_reader_policy as old_policy
from mfdataindia.review import reader_function_attestation as collector
from mfdataindia.review import reader_function_attestation_inventory as inv
from mfdataindia.review import reader_function_attestation_reference as reference
from mfdataindia.review import reader_function_attestation_validation as validation
from test_reader_access_correction_postgres import fixture_db as fixture_db, owner, rows
from test_reader_function_identification_postgres import ident_db as ident_db, Recorder

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
SIGNATURE = "mf_fact_review.dependencies(integer)"


def create_statement():
    raw = (ROOT / "sql/optional/036_official_fact_verification.sql").read_text()
    start = "CREATE FUNCTION mf_fact_review.dependencies(p_code integer) RETURNS jsonb"
    end = "\nCREATE FUNCTION mf_fact_review.record_review"
    assert raw.count(start) == raw.count(end) == 1
    return raw[raw.index(start):raw.index(end)]


@pytest.fixture
def attest_db(ident_db):
    with owner(ident_db) as conn:
        conn.execute(create_statement())
        conn.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM PUBLIC")
        conn.execute("GRANT USAGE ON SCHEMA mf_fact_review TO mfdata_verified_reader")
        conn.execute("GRANT EXECUTE ON FUNCTION " + SIGNATURE
                     + " TO mfdata_verified_reader,mfdata_verified_admin")
    return ident_db


def raw_capture(dsn):
    with owner(dsn) as conn:
        recorder = Recorder(conn)
        with collector._capture(recorder) as value:
            result = deepcopy(value)
        assert conn.info.transaction_status == TransactionStatus.IDLE
        assert recorder.calls == [*inv.CONTROLS, inv.CONTEXT_SQL, inv.ATTESTATION_SQL,
                                  inv.ATTESTATION_SQL, inv.CONTEXT_SQL, inv.TERMINAL]
    return result["attestation"]


def catalogue(dsn):
    with owner(dsn) as conn:
        return conn.execute(old_policy.STATE_SQL).fetchone()["value"]


def only(doc):
    assert len(doc["functions"]) == 1
    return doc["functions"][0]


def test_native_exact_committed_body_all_declaration_seals_and_full_preservation(attest_db):
    before, source = catalogue(attest_db), rows(attest_db)
    doc = raw_capture(attest_db)
    f = only(doc)
    assert f["name"] == "dependencies" and f["schema"] == "mf_fact_review"
    assert reference.comparison(f)["declaration_matches_committed_reference"] is True
    with owner(attest_db) as conn:
        actual = conn.execute(
            "SELECT p.oid::bigint AS oid, "
            "encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') AS body, "
            "octet_length(convert_to(p.prosrc,'UTF8')) AS body_bytes, "
            "encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex') AS definition, "
            "octet_length(convert_to(pg_get_functiondef(p.oid),'UTF8')) AS definition_bytes "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='mf_fact_review' AND p.proname='dependencies'").fetchone()
        assert f["oid"] == actual["oid"] != 41849
        assert f["body_sha256"] == actual["body"] == reference.BODY_SHA256
        assert f["body_bytes"] == actual["body_bytes"] == reference.BODY_BYTES
        assert f["definition_sha256"] == actual["definition"]
        assert f["definition_bytes"] == actual["definition_bytes"]
        assert conn.execute("SELECT count(*) AS n FROM mf.fn_write_tripwire").fetchone()["n"] == 0
    assert catalogue(attest_db) == before and rows(attest_db) == source
    with pytest.raises(ValueError):
        validation.validate_document(doc)  # Actual ephemeral IDs NEVER become public evidence.


@pytest.mark.parametrize("mutation", ["cost", "volatile", "strict", "parallel", "config_null",
                                     "config_private", "body", "argname", "default"])
def test_native_real_declaration_drift_sealed_and_reported_nonaccepting(attest_db, mutation):
    with owner(attest_db) as conn:
        if mutation in ("argname", "default"):
            conn.execute("DROP FUNCTION " + SIGNATURE)
            statement = create_statement().replace(
                "p_code integer)", "p_other integer)" if mutation == "argname"
                else "p_code integer DEFAULT 1)")
            conn.execute(statement)
            conn.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM PUBLIC")
            conn.execute("GRANT EXECUTE ON FUNCTION " + SIGNATURE
                         + " TO mfdata_verified_reader,mfdata_verified_admin")
        elif mutation == "body":
            conn.execute(create_statement().replace(
                "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION").replace(
                "END $$;", "END /* PRIVATE_BODY_SENTINEL */ $$;"))
        else:
            alter = {"cost": "COST 101", "volatile": "VOLATILE", "strict": "STRICT",
                     "parallel": "PARALLEL SAFE", "config_null": "RESET ALL",
                     "config_private": "SET search_path TO private_marker,pg_catalog"}[mutation]
            conn.execute("ALTER FUNCTION " + SIGNATURE + " " + alter)
    before, source = catalogue(attest_db), rows(attest_db)
    doc = raw_capture(attest_db)
    f = only(doc)
    report = reference.comparison(f)
    assert report["declaration_matches_committed_reference"] is False
    assert report["function_invoked"] is report["permission_acceptance"] is False
    if mutation != "body":
        assert report["body_matches_committed_reference"] is True
    if mutation == "default":
        assert f["argument_default_count"] == 1 and f["argument_defaults_is_null"] is False
        with owner(attest_db) as conn:
            actual = conn.execute(
                "SELECT pg_get_expr(proargdefaults,0,false) AS text FROM pg_proc "
                "WHERE oid='mf_fact_review.dependencies(integer)'::regprocedure").fetchone()["text"]
        assert f["argument_defaults_sha256"] == hashlib.sha256(actual.encode()).hexdigest()
        assert f["argument_defaults_bytes"] == len(actual.encode())
    assert all(x not in json.dumps(doc) for x in ("PRIVATE_BODY_SENTINEL", "private_marker"))
    assert catalogue(attest_db) == before and rows(attest_db) == source


def test_native_private_configuration_sealed_before_boundary_and_unknown_value_not_echoed(attest_db):
    with psycopg.connect(attest_db, autocommit=True) as admin:
        admin.execute("ALTER FUNCTION " + SIGNATURE
                      + " SET synthetic.private_config='PRIVATE_CONFIG_SENTINEL'")
    before = catalogue(attest_db)
    doc = raw_capture(attest_db)
    f = only(doc)
    assert f["configuration_is_null"] is False
    assert f["configuration_is_exact_catalog_search_path"] is False
    assert reference.comparison(f)["settings_match_committed_reference"] is False
    assert "PRIVATE_CONFIG_SENTINEL" not in json.dumps(doc)
    with owner(attest_db) as conn:
        actual = conn.execute(
            "SELECT array_to_json(proconfig)::text AS text FROM pg_proc "
            "WHERE oid='mf_fact_review.dependencies(integer)'::regprocedure").fetchone()["text"]
    assert f["configuration_sha256"] == hashlib.sha256(actual.encode()).hexdigest()
    assert f["configuration_bytes"] == len(actual.encode())
    assert catalogue(attest_db) == before


@pytest.mark.parametrize("mutation", ["invoker", "revoke", "outside", "overload"])
def test_native_fixed_predicate_and_scope_never_invokes_or_substitutes_target(attest_db, mutation):
    with owner(attest_db) as conn:
        if mutation == "invoker":
            conn.execute("ALTER FUNCTION " + SIGNATURE + " SECURITY INVOKER")
        elif mutation == "revoke":
            conn.execute("REVOKE EXECUTE ON FUNCTION " + SIGNATURE + " FROM mfdata_verified_reader")
        elif mutation == "outside":
            conn.execute("ALTER FUNCTION " + SIGNATURE + " SET SCHEMA mf")
        else:
            conn.execute("CREATE FUNCTION mf_fact_review.dependencies(text) RETURNS jsonb "
                         "LANGUAGE SQL SECURITY DEFINER AS $$ SELECT '{}'::jsonb $$")
    before = catalogue(attest_db)
    doc = raw_capture(attest_db)
    assert len(doc["functions"]) == (1 if mutation == "overload" else 0)
    assert catalogue(attest_db) == before


def test_native_empty_and_null_seal_expressions_are_not_normalized(attest_db):
    with owner(attest_db) as conn:
        values = conn.execute(
            "SELECT array_to_json(NULL::text[])::text AS null_array,"
            "array_to_json(ARRAY[]::text[])::text AS empty_array,"
            "octet_length(convert_to('', 'UTF8')) AS empty_bytes").fetchone()
    assert values == {"null_array": None, "empty_array": "[]", "empty_bytes": 0}
    with owner(attest_db) as conn:
        conn.execute("ALTER FUNCTION " + SIGNATURE + " RESET ALL")
    f = only(raw_capture(attest_db))
    assert f["configuration_is_null"] is True
    assert f["configuration_sha256"] is None and f["configuration_bytes"] == 0
    assert f["argument_defaults_is_null"] is True
    assert f["argument_defaults_sha256"] is None and f["argument_defaults_bytes"] == 0


def test_native_rows_and_retset_are_explicit_not_hidden_behind_body_digest(attest_db):
    with owner(attest_db) as conn:
        conn.execute("DROP FUNCTION " + SIGNATURE)
        conn.execute("CREATE FUNCTION mf_fact_review.dependencies(p_code integer) RETURNS SETOF jsonb "
                     "LANGUAGE plpgsql STABLE SECURITY DEFINER ROWS 2 SET search_path=pg_catalog "
                     "AS $$ BEGIN RETURN QUERY SELECT '{}'::jsonb; END $$")
        conn.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM PUBLIC")
        conn.execute("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO mfdata_verified_reader")
    f = only(raw_capture(attest_db))
    assert f["returns_set"] is True and f["rows"] == "2"
    assert reference.comparison(f)["declaration_matches_committed_reference"] is False
