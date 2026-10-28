"""Actual fixed SQL in disposable CI PG18; no production-profile OID substitution."""
from copy import deepcopy
import json

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
import pytest

from mfdataindia.review import broad_reader_policy as old_policy
from mfdataindia.review import reader_function_identification as collector
from mfdataindia.review import reader_function_identification_inventory as inv
from mfdataindia.review import reader_function_identification_validation as validation
from test_reader_access_correction_postgres import fixture_db as fixture_db, owner, rows

pytestmark = pytest.mark.postgres
SIGNATURE = "mf.diag_fn(integer,text,integer)"


@pytest.fixture
def ident_db(fixture_db):
    with psycopg.connect(fixture_db, autocommit=True) as admin:
        for name in validation.SCHEMAS:
            if name not in ("mf", "mf_review"):
                admin.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION mfdata_admin").format(
                    sql.Identifier(name)))
    with owner(fixture_db) as conn:
        conn.execute("CREATE TABLE mf.fn_write_tripwire(value integer)")
        conn.execute("CREATE FUNCTION mf.diag_fn(integer,text,integer) RETURNS integer "
                     "LANGUAGE SQL SECURITY DEFINER AS "
                     "$$ SELECT 1 /* PRIVATE_BODY_SENTINEL */ $$")
    return fixture_db


@pytest.fixture
def groups(ident_db):
    names = ("diag_ident_group_one", "diag_ident_group_two")
    with psycopg.connect(ident_db, autocommit=True) as admin:
        for name in names:
            admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(name)))
        try:
            yield ident_db, names
        finally:
            for name in reversed(names):
                admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(name)))
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))


class Recorder:
    def __init__(self, conn):
        self.conn, self.calls = conn, []

    @property
    def autocommit(self):
        return self.conn.autocommit

    @property
    def info(self):
        return self.conn.info

    def execute(self, statement, *, prepare=False):
        self.calls.append(statement)
        return self.conn.execute(statement, prepare=prepare)


def raw_capture(dsn):
    with owner(dsn) as conn:
        recorder = Recorder(conn)
        with collector._capture(recorder) as value:
            result = deepcopy(value)
        assert conn.info.transaction_status == TransactionStatus.IDLE
        assert recorder.calls == [*inv.CONTROLS, inv.CONTEXT_SQL, inv.IDENTIFICATION_SQL,
                                  inv.IDENTIFICATION_SQL, inv.CONTEXT_SQL, inv.TERMINAL]
    return result["identification"]


def catalogue(dsn):
    with owner(dsn) as conn:
        return conn.execute(old_policy.STATE_SQL).fetchone()["value"]


def target(value):
    matches = [f for f in value["functions"] if f["schema"] == "mf" and f["name"] == "diag_fn"]
    assert len(matches) == 1
    return matches[0]


def test_native_exact_SQL_public_default_types_private_secrecy_and_whole_preservation(ident_db):
    with psycopg.connect(ident_db, autocommit=True) as admin:
        admin.execute("ALTER FUNCTION mf.diag_fn(integer,text,integer) "
                      "SET synthetic.private_config='PRIVATE_CONFIG_SENTINEL'")
        admin.execute("ALTER ROLE mfdata_verified_reader "
                      "SET synthetic.private_role='PRIVATE_ROLE_SENTINEL'")
    before, before_rows = catalogue(ident_db), rows(ident_db)
    value = raw_capture(ident_db)
    f = target(value)
    assert f["acl_is_null"] is True
    assert f["argument_type_oids"] == [23, 25, 23]
    assert f["return_type_oid"] == 23 and type(f["oid"]) is int
    assert all(type(a[k]) is int for a in f["execute_acl_atoms"]
               for k in ("grantee_oid", "grantor_oid"))
    public = next(a for a in f["execute_acl_atoms"] if a["grantee_oid"] == 0)
    assert public["grantee_name"] == "PUBLIC" and public["role_privileges_immediately_available"]
    assert public["reader_is_member"] is public["reader_can_set_role"] is False
    raw = json.dumps(value)
    assert all(s not in raw for s in ("PRIVATE_BODY_SENTINEL", "PRIVATE_CONFIG_SENTINEL",
                                     "PRIVATE_ROLE_SENTINEL", "prosrc", "proconfig", "rolconfig"))
    assert catalogue(ident_db) == before and rows(ident_db) == before_rows
    with owner(ident_db) as conn:
        actual_oid = conn.execute("SELECT oid::bigint AS oid FROM pg_roles "
                                  "WHERE rolname='mfdata_verified_reader'").fetchone()["oid"]
        assert value["reader"][0]["oid"] == actual_oid != 40032
    with pytest.raises(ValueError):
        validation.validate_document(value)  # Native data is NOT normalized into production evidence.


@pytest.mark.parametrize("route", ["public_stored", "direct"])
def test_native_stored_public_and_direct_reader_EXECUTE(ident_db, route):
    with owner(ident_db) as conn:
        conn.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM PUBLIC")
        conn.execute("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO " +
                     ("PUBLIC" if route == "public_stored" else "mfdata_verified_reader"))
    value, before = raw_capture(ident_db), catalogue(ident_db)
    f = target(value)
    assert f["acl_is_null"] is False and f["effective_execute"] is True
    who = 0 if route == "public_stored" else value["reader"][0]["oid"]
    assert any(a["grantee_oid"] == who and a["role_privileges_immediately_available"]
               for a in f["execute_acl_atoms"])
    assert catalogue(ident_db) == before


@pytest.mark.parametrize("inherit,can_set", [(True, True), (True, False), (False, True), (False, False)])
def test_native_actual_INHERIT_not_MEMBER_or_SET_is_current_route(groups, inherit, can_set):
    dsn, names = groups
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("GRANT {} TO mfdata_verified_reader WITH INHERIT " +
                             ("TRUE" if inherit else "FALSE")).format(sql.Identifier(names[0])))
        admin.execute(sql.SQL("GRANT {} TO mfdata_verified_reader WITH SET " +
                             ("TRUE" if can_set else "FALSE")).format(sql.Identifier(names[0])))
    with owner(dsn) as conn:
        conn.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM PUBLIC")
        conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO {}").format(
            sql.Identifier(names[0])))
        effective = conn.execute(
            "SELECT has_function_privilege('mfdata_verified_reader',"
            "'mf.diag_fn(integer,text,integer)','EXECUTE') AS value").fetchone()["value"]
    value = raw_capture(dsn)
    assert effective is inherit
    if inherit:
        f = target(value)
        a = next(a for a in f["execute_acl_atoms"] if a["grantee_name"] == names[0])
        assert a["reader_is_member"] and a["role_privileges_immediately_available"]
        assert a["reader_can_set_role"] is can_set
    else:
        assert value["functions"] == []


def test_native_multiple_simultaneous_routes_are_all_retained(groups):
    dsn, names = groups
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("GRANT {} TO mfdata_verified_reader WITH INHERIT TRUE").format(
            sql.Identifier(names[0])))
    with owner(dsn) as conn:
        conn.execute("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO mfdata_verified_reader")
        conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO {}").format(
            sql.Identifier(names[0])))
    value = raw_capture(dsn)
    atoms = target(value)["execute_acl_atoms"]
    assert {a["grantee_name"] for a in atoms} >= {"PUBLIC", "mfdata_verified_reader", names[0]}
    assert all(a["role_privileges_immediately_available"] for a in atoms
               if a["grantee_name"] in {"PUBLIC", "mfdata_verified_reader", names[0]})


def test_native_owner_lost_EXECUTE_not_assumed_current_route(ident_db):
    with psycopg.connect(ident_db, autocommit=True) as admin:
        admin.execute("ALTER FUNCTION " + SIGNATURE + " OWNER TO mfdata_verified_reader")
        admin.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM PUBLIC,mfdata_verified_reader")
        assert admin.execute(
            "SELECT has_function_privilege('mfdata_verified_reader',"
            "'mf.diag_fn(integer,text,integer)','EXECUTE')").fetchone()[0] is False
    assert raw_capture(ident_db)["functions"] == []


def test_native_superuser_empty_ACL_bypass_is_explicit(ident_db):
    with psycopg.connect(ident_db, autocommit=True) as admin:
        admin.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM PUBLIC,mfdata_admin")
        admin.execute("ALTER ROLE mfdata_verified_reader SUPERUSER")
    value = raw_capture(ident_db)
    assert value["reader"][0]["rolsuper"] is True
    assert target(value)["execute_acl_atoms"] == []


def test_native_outside_and_invoker_routines_not_in_matching_scope(ident_db):
    with psycopg.connect(ident_db, autocommit=True) as admin:
        admin.execute("CREATE FUNCTION public.outside_diag() RETURNS integer "
                      "LANGUAGE SQL SECURITY DEFINER AS $$SELECT 1$$")
    with owner(ident_db) as conn:
        conn.execute("CREATE FUNCTION mf.invoker_diag() RETURNS integer LANGUAGE SQL AS $$SELECT 1$$")
    assert [(f["schema"], f["name"]) for f in raw_capture(ident_db)["functions"]] == [("mf", "diag_fn")]


def test_native_schema_USAGE_separate_from_effective_EXECUTE(ident_db):
    with owner(ident_db) as conn:
        conn.execute("REVOKE USAGE ON SCHEMA mf FROM mfdata_verified_reader")
    f = target(raw_capture(ident_db))
    assert f["effective_execute"] is True and f["schema_usage"] is False


@pytest.mark.parametrize("kind", ["trigger", "event_trigger", "procedure"])
def test_native_routine_type_identity_without_invoking_function(ident_db, kind):
    with owner(ident_db) as conn:
        conn.execute("CREATE FUNCTION mf.diag_write_tripwire() RETURNS void LANGUAGE SQL "
                     "SECURITY DEFINER AS $$INSERT INTO mf.fn_write_tripwire VALUES(1)$$")
        if kind == "procedure":
            conn.execute("CREATE PROCEDURE mf.diag_special() LANGUAGE plpgsql SECURITY DEFINER "
                         "AS $$BEGIN RETURN; END$$")
        elif kind == "trigger":
            conn.execute("CREATE FUNCTION mf.diag_special() RETURNS trigger LANGUAGE plpgsql "
                         "SECURITY DEFINER AS $$BEGIN RETURN NEW; END$$")
        else:
            conn.execute("CREATE FUNCTION mf.diag_special() RETURNS event_trigger LANGUAGE plpgsql "
                         "SECURITY DEFINER AS $$BEGIN RETURN; END$$")
    value = raw_capture(ident_db)
    special = next(f for f in value["functions"] if f["name"] == "diag_special")
    assert special["kind"] == ("p" if kind == "procedure" else "f")
    assert special["return_type_oid"] == {"trigger": 2279, "event_trigger": 3838,
                                         "procedure": 2278}[kind]
    with owner(ident_db) as conn:
        assert conn.execute("SELECT count(*) AS n FROM mf.fn_write_tripwire").fetchone()["n"] == 0


@pytest.mark.parametrize("missing", ["none_matching", "reader", "schema"])
def test_native_zero_or_missing_identity_retained_not_silently_complete(ident_db, missing):
    rename = missing == "reader"
    with psycopg.connect(ident_db, autocommit=True) as admin:
        try:
            if rename:
                admin.execute("ALTER ROLE mfdata_verified_reader RENAME TO diag_hidden_reader")
            elif missing == "schema":
                admin.execute("DROP SCHEMA mf_rolling")
            else:
                admin.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM PUBLIC")
            value = raw_capture(ident_db)
            if rename:
                assert value["reader"] == [] and value["functions"] == []
            elif missing == "schema":
                assert len(value["schemas"]) == 15
            else:
                assert value["functions"] == []
            with pytest.raises(ValueError):
                validation.validate_document(value)  # No ephemeral profile admission, even at zero.
        finally:
            if rename:
                admin.execute("ALTER ROLE diag_hidden_reader RENAME TO mfdata_verified_reader")


def test_native_public_strict_profile_rejection_rolls_back_without_changing_SQL(ident_db):
    before, before_rows = catalogue(ident_db), rows(ident_db)
    with owner(ident_db) as conn:
        recorder = Recorder(conn)
        with pytest.raises(ValueError):
            collector.Capture().run(recorder)
        assert inv.IDENTIFICATION_SQL in recorder.calls
        assert recorder.calls[-1] == inv.TERMINAL and recorder.calls.count(inv.TERMINAL) == 1
        assert conn.info.transaction_status == TransactionStatus.IDLE
    assert catalogue(ident_db) == before and rows(ident_db) == before_rows
