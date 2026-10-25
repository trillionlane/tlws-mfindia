"""Disposable native CI-only actual recursive lock witnesses; no live approval."""

from copy import deepcopy

import psycopg
import pytest

from mfdataindia.review import reader_access_correction as c
from mfdataindia.review import reader_access_owner_connection as g
from test_reader_access_correction_postgres import fixture_db as fixture_db
from test_reader_access_correction_postgres import fixture_change, owner, rows, state
from test_reader_access_transport import window

pytestmark = pytest.mark.postgres


def witness(plan, names):
    return deepcopy(
        [
            {k: r[k] for k in ("oid", "nspname", "relname", "relkind", "owner", "definition")}
            for r in plan["before"]["relations"]
            if r["nspname"] == "mf" and r["relname"] in names
        ]
    )


@pytest.mark.parametrize("shape", ["base", "nested", "inherited"])
def test_native_actual_recursive_witness_precedes_exact_grant_and_preserves_rows(fixture_db, shape):
    names = {"current_fund_facts", "fund_variants", "facts_fixture"}
    with owner(fixture_db) as conn:
        if shape == "nested":
            conn.execute("CREATE VIEW mf.nested_fixture AS SELECT * FROM mf.facts_fixture")
            conn.execute(
                "CREATE OR REPLACE VIEW mf.current_fund_facts AS SELECT * FROM mf.nested_fixture"
            )
            names.add("nested_fixture")
        elif shape == "inherited":
            conn.execute("CREATE TABLE mf.child_fixture () INHERITS (mf.facts_fixture)")
            names.add("child_fixture")
    plan = fixture_change(fixture_db)
    original = rows(fixture_db)
    wanted = witness(plan, names)
    with owner(fixture_db) as conn:
        gate = g._GateConnection(conn, plan, wanted, window=window())
        result = c._execute(gate, plan)  # Same frozen private mechanics; ephemeral OIDs only.
        assert gate.lock_witness == wanted
    assert result["stage"] == "COMMITTED_VERIFIED"
    assert state(fixture_db) == plan["after"] and rows(fixture_db) == original


@pytest.mark.parametrize("defect", ["missing", "extra", "oid", "owner", "definition"])
def test_native_unapproved_actual_lock_witness_aborts_before_any_grant(fixture_db, defect):
    plan = fixture_change(fixture_db)
    wanted = witness(plan, {"current_fund_facts", "fund_variants", "facts_fixture"})
    if defect == "missing":
        wanted.pop()
    elif defect == "extra":
        wanted.append(deepcopy(wanted[-1]))
    elif defect == "definition":
        next(r for r in wanted if r["relkind"] == "v")["definition"]["sha256"] = "0" * 64
    else:
        wanted[0][defect] += 1
    original = rows(fixture_db)
    with (
        owner(fixture_db) as conn,
        pytest.raises(c.ChangeFailure, match="PRECOMMIT_ABORTED_NO_RETRY"),
    ):
        c._execute(g._GateConnection(conn, plan, wanted, window=window()), plan)
    assert state(fixture_db) == plan["before"] and rows(fixture_db) == original


def test_native_underlying_busy_lock_nowait_does_not_wait_or_grant(fixture_db):
    plan = fixture_change(fixture_db)
    wanted = witness(plan, {"current_fund_facts", "fund_variants", "facts_fixture"})
    with psycopg.connect(fixture_db) as other:
        other.execute("LOCK TABLE mf.facts_fixture IN ACCESS SHARE MODE")
        with (
            owner(fixture_db) as conn,
            pytest.raises(c.ChangeFailure, match="PRECOMMIT_ABORTED_NO_RETRY"),
        ):
            c._execute(g._GateConnection(conn, plan, wanted, window=window()), plan)
    assert state(fixture_db) == plan["before"]
