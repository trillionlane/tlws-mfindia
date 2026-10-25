"""Fixed adapter order/lock witness/ambiguity and whole-wall regression tests."""

from copy import deepcopy

import pytest

from mfdataindia.review import reader_access_correction as c
from mfdataindia.review import reader_access_owner_connection as g
from test_reader_access_correction import Connection
from test_reader_access_transport import proposal


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def fetchall(self):
        return deepcopy(self.rows)


class WitnessConnection(Connection):
    def __init__(self, p, *, locks=None, **kw):
        super().__init__(p["change"], **kw)
        self.locks = deepcopy(p["lock_witness"] if locks is None else locks)

    def execute(self, statement, *, prepare=False):
        if statement == g.LOCK_WITNESS_SQL:
            self.calls.append(statement)
            return Rows(self.locks)
        return super().execute(statement, prepare=prepare)


def test_exact_own_lock_witness_is_inside_frozen_engine_before_first_grant():
    p = proposal()
    conn = WitnessConnection(p)
    gate = g.guarded_connection(conn, p)
    receipt = c.run_change(gate, c.Permit(p["change"], c.fingerprint(p["change"])))
    index = conn.calls.index(g.LOCK_WITNESS_SQL)
    assert conn.calls[index - 1].startswith("LOCK TABLE ONLY")
    assert conn.calls.index(next(s for s in conn.calls if s.startswith("GRANT"))) > index
    assert conn.calls.count(g.LOCK_WITNESS_SQL) == 1
    assert receipt["stage"] == "COMMITTED_VERIFIED" and gate.lock_witness == p["lock_witness"]
    returned = gate.lock_witness
    returned.clear()
    assert gate.lock_witness == p["lock_witness"]


@pytest.mark.parametrize("defect", ["missing", "extra", "oid", "owner", "definition", "bool_oid"])
def test_any_incomplete_or_drifted_actual_witness_rolls_back_without_grant(defect):
    p = proposal()
    locks = deepcopy(p["lock_witness"])
    if defect == "missing":
        locks.pop()
    elif defect == "extra":
        locks.append(locks[-1])
    elif defect == "bool_oid":
        locks[0]["oid"] = True
    elif defect == "definition":
        locks[-1]["definition"]["sha256"] = "0" * 64
    else:
        locks[0][defect] += 1
    conn = WitnessConnection(p, locks=locks)
    gate = g.guarded_connection(conn, p)
    with pytest.raises(c.ChangeFailure, match="PRECOMMIT_ABORTED_NO_RETRY"):
        c.run_change(gate, c.Permit(p["change"], c.fingerprint(p["change"])))
    assert not any(s.startswith("GRANT") for s in conn.calls)
    assert conn.calls.count("ROLLBACK") == 1 and "COMMIT" not in conn.calls


def test_adapter_rejects_grant_before_locks_or_arbitrary_SQL_with_zero_connection_calls():
    p = proposal()
    conn = WitnessConnection(p)
    gate = g.guarded_connection(conn, p)
    for query in [
        g._grants(p["change"])[0],
        "SET ROLE postgres",
        "SELECT * FROM mf.current_fund_facts",
        "REVOKE ALL ON mf.fund_variants FROM mfdata_verified_reader",
    ]:
        with pytest.raises((ValueError, c.ChangeFailure)):
            gate.execute(query)
    assert conn.calls == []


def test_commit_ambiguity_has_no_rollback_reconnect_reversal_or_further_IO():
    p = proposal()
    conn = WitnessConnection(p, fail="COMMIT")
    gate = g.guarded_connection(conn, p)
    with pytest.raises(c.ChangeFailure, match="COMMIT_OUTCOME_UNKNOWN"):
        c.run_change(gate, c.Permit(p["change"], c.fingerprint(p["change"])))
    saved = list(conn.calls)
    for query in ("COMMIT", "ROLLBACK", c.STATE_SQL):
        with pytest.raises(c.ChangeFailure, match="COMMIT_OUTCOME_UNKNOWN"):
            gate.execute(query)
    assert conn.calls == saved and conn.calls.count("COMMIT") == 1 and "ROLLBACK" not in conn.calls


def test_wall_timeout_at_witness_has_no_cleanup_SQL_after_spent_budget(monkeypatch):
    p = proposal()
    conn = WitnessConnection(p)
    original = conn.execute

    def execute(statement, *, prepare=False):
        if statement == g.LOCK_WITNESS_SQL:
            conn.calls.append(statement)
            raise c.WallTimeout("SYNTHETIC_SPENT_WALL")
        return original(statement, prepare=prepare)

    monkeypatch.setattr(conn, "execute", execute)
    with pytest.raises(c.ChangeFailure, match="WALL_TIMEOUT_NO_RETRY"):
        c.run_change(
            g.guarded_connection(conn, p), c.Permit(p["change"], c.fingerprint(p["change"]))
        )
    assert (
        conn.calls[-1] == g.LOCK_WITNESS_SQL
        and "ROLLBACK" not in conn.calls
        and "COMMIT" not in conn.calls
    )
