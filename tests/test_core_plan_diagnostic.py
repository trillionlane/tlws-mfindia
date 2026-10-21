"""Provider-free worker guards; not native database or cloud execution evidence."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path

import pytest


WORKER = Path(__file__).parents[1] / "scripts/diagnose_core_fund_plan_dev.py"
spec = importlib.util.spec_from_file_location("core_plan_diagnostic", WORKER)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        assert len(self.rows) == 1
        return self.rows[0]

    def fetchall(self):
        return self.rows


def context():
    return {"effective": "mfdata_verified_reader", "authenticated": "mfdata_verified_reader",
            "database": "mfdataindia", "read_only": "on", "isolation": "repeatable read",
            "statement_timeout": "5s", "lock_timeout": "5s", "transaction_timeout": "15s",
            "snapshot": "100:101:"}


class Connection:
    def __init__(self, *, own=None, drift=None, plan=None):
        self.calls = []
        self.own = own if own is not None else []
        self.drift = drift
        self.contexts = 0
        self.plan = [{"Plan": {"Node Type": "Result", "Plan Rows": 1}}] if plan is None else plan

    def execute(self, sql, bindings=None):
        self.calls.append((sql, bindings))
        if sql.startswith("SET "):
            return Rows([])
        if sql == worker.CONTEXT_SQL:
            self.contexts += 1
            row = context()
            if self.drift and self.contexts > 1:
                row.update(self.drift)
            return Rows([row])
        if sql.startswith("EXPLAIN (FORMAT JSON) "):
            assert bindings in ({"code": 154117}, {"codes": [154117]})
            return Rows([{"QUERY PLAN": deepcopy(self.plan)}])
        assert sql == worker.queries._FACTS_SELECT
        assert bindings == {"code": 154117}
        return Rows(self.own)


@pytest.fixture
def verified(monkeypatch):
    # Mock only the database-bound guard helpers, not the original SQL or
    # worker context/identity/transport checks; all calls still inspected.
    calls = []
    monkeypatch.setattr(worker, "verify_runtime", lambda conn, purpose: calls.append(purpose))
    cfg = {"generation": 29, "policy_hash": worker.fingerprint(worker.book_sources.current_policy(4))}
    monkeypatch.setattr(worker.current, "configuration", lambda conn: dict(cfg))
    return calls, cfg


def test_unchanged_original_queries_exact_real_code_no_rewrite():
    statements = worker.fixed_queries()
    assert [r[0] for r in statements] == ["own_facts", "single_sibling", "batch_sibling"]
    assert [r[1] for r in statements] == [worker.queries._FACTS_SELECT,
                                        worker.queries._SIBLING_SOURCE_SQL,
                                        worker.queries._BATCH_SIBLING_SOURCE_SQL]
    assert [r[2] for r in statements] == [{"code": 154117}, {"code": 154117}, {"codes": [154117]}]


@pytest.mark.parametrize("own,eligible", [([], True), ([{"aum": None}], True),
                                         ([{"aum": 0}], False), ([{"benchmark": "Index"}], False)])
def test_full_plans_no_sibling_execution_and_one_own_select(verified, own, eligible):
    conn = Connection(own=own)
    result = worker.diagnose(conn)
    assert verified[0] == ["api"]
    assert [sql for sql, _ in conn.calls[:4]] == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        "SET LOCAL statement_timeout='5s'", "SET LOCAL lock_timeout='5s'",
        "SET LOCAL transaction_timeout='15s'",
    ]
    actual = [(q, p) for q, p in conn.calls if not q.startswith(("SET ", "EXPLAIN "))
              and q != worker.CONTEXT_SQL]
    assert actual == [(worker.queries._FACTS_SELECT, {"code": 154117})]
    assert len(result["plans"]) == 3
    for entry in result["plans"]:
        assert entry["complete_plan"] == conn.plan
        assert entry["complete_plan_hash"] == worker.fingerprint(conn.plan)
        assert entry["ANALYZE"] is False
    assert result["current_own_facts_trigger_fallback"] is eligible
    assert result["original_own_facts_rows"] == len(own)
    assert result["actual_sibling_queries_executed"] == 0
    assert result["historical_K_plan_or_core_role_proved"] is False
    assert result["performance_acceptance"] is False
    assert result["rollout_acceptance"] is False


@pytest.mark.parametrize("field,value", [
    ("effective", "mfdata_admin"), ("authenticated", "postgres"), ("database", "other"),
    ("read_only", "off"), ("isolation", "read committed"),
    ("statement_timeout", "30s"), ("lock_timeout", "30s"),
    ("transaction_timeout", "60s"), ("snapshot", ""),
])
def test_context_drift_rejected_without_retry(verified, field, value):
    conn = Connection(drift={field: value})
    with pytest.raises(ValueError):
        worker.diagnose(conn)
    assert sum(q == worker.queries._FACTS_SELECT for q, _ in conn.calls) == 1


def test_missing_context_key_rejected():
    class Missing:
        def execute(self, *_):
            row = context()
            del row["snapshot"]
            return Rows([row])
    with pytest.raises(ValueError, match="complete diagnostic"):
        worker.checked_context(Missing())


@pytest.mark.parametrize("plan", [[], [{"other": 1}], [{"Plan": {}, "Execution Time": 1}], [{"Plan": {}}, {"Plan": {}}]])
def test_incomplete_or_executed_plan_rejected_before_actual_select(verified, plan):
    conn = Connection(plan=plan)
    with pytest.raises(ValueError):
        worker.diagnose(conn)
    assert not any(q == worker.queries._FACTS_SELECT for q, _ in conn.calls)


def test_duplicate_own_facts_rejected_no_retry(verified):
    conn = Connection(own=[{"aum": None}, {"aum": None}])
    with pytest.raises(ValueError, match="duplicate own facts"):
        worker.diagnose(conn)
    assert sum(q == worker.queries._FACTS_SELECT for q, _ in conn.calls) == 1


def test_wrong_policy_stops_before_plans(verified):
    verified[1]["policy_hash"] = "0" * 64
    conn = Connection()
    with pytest.raises(ValueError, match="source4"):
        worker.diagnose(conn)
    assert not any(q.startswith("EXPLAIN") for q, _ in conn.calls)


def test_wrong_query_source_hash_stops_before_database(monkeypatch):
    monkeypatch.setattr(worker, "QUERY_FILE_SHA256", "0" * 64)
    conn = Connection()
    with pytest.raises(ValueError, match="query module"):
        worker.diagnose(conn)
    assert conn.calls == []


def test_database_exception_propagates_without_retry(verified):
    class Failed(Connection):
        def execute(self, sql, bindings=None):
            if sql.startswith("EXPLAIN"):
                self.calls.append((sql, bindings))
                raise TimeoutError("synthetic bounded database failure")
            return super().execute(sql, bindings)
    conn = Failed()
    with pytest.raises(TimeoutError):
        worker.diagnose(conn)
    assert sum(q.startswith("EXPLAIN") for q, _ in conn.calls) == 1
    assert not any(q == worker.queries._FACTS_SELECT for q, _ in conn.calls)


def test_main_has_no_target_sql_timeout_or_retry_cli_options():
    source = WORKER.read_text()
    assert ".add_argument(" not in source
    assert "EXPLAIN (ANALYZE" not in source
    assert "gcloud" not in source


def test_own_hash_handles_actual_decimal_date_types_without_float_rounding():
    row = {"aum": Decimal("12345678901234567890.12345"), "inception_date": date(2026, 9, 30)}
    assert worker.own_facts_hash(row) == worker.own_facts_hash(dict(row))
    assert worker.own_facts_hash(row) != worker.own_facts_hash({**row, "aum": str(row["aum"])})
    with pytest.raises(TypeError):
        worker.own_facts_hash({"unknown": object()})
