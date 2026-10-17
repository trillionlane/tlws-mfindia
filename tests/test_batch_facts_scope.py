"""Batch facts work must follow, and be bounded by, identifier resolution."""

from datetime import date
from decimal import Decimal

import pytest

from mfdataindia.api import queries


def _fund(code, isin):
    return {
        "amfi_scheme_code": code, "scheme_name": f"Fund {code}",
        "plan_type": "REGULAR", "option_type": "GROWTH",
        "scheme_category": "Equity", "isin_primary": isin,
        "isin_growth_or_div_payout": isin, "isin_div_reinvest": None,
        "in_scope": True, "is_defunct": False, "amfi_amc_name": "AMC",
        "latest_nav": Decimal("101.25"), "latest_nav_date": date(2026, 9, 30),
    }


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, funds, facts, *, expected_codes=None):
        self.funds = funds
        self.facts = facts
        self.calls = []
        self.expected_codes = expected_codes

    def execute(self, query, params):
        self.calls.append((query, params))
        if len(self.calls) == 1:
            # No unconstrained facts join in the mixed code/ISIN lookup.
            assert "mf.current_fund_facts" not in query
            return _Rows(self.funds)
        assert len(self.calls) == 2
        assert "FROM mf.current_fund_facts" in query
        assert "unnest(%(codes)s::int[]) AS requested(code)" in query
        assert "CROSS JOIN LATERAL" in query
        assert "WHERE amfi_scheme_code = requested.code OFFSET 0" in query
        codes = self.expected_codes if self.expected_codes is not None else sorted({r["amfi_scheme_code"] for r in self.funds})
        assert params == {"codes": codes}
        return _Rows(self.facts)


@pytest.mark.parametrize("ids", [
    ["20", "10", "missing", "20"],
    ["INF20", "inf10", "missing", "INF20"],
    ["INF20", "20", "10", "missing"],
])
def test_batch_facts_predicate_uses_only_resolved_codes(ids):
    # Deliberately unordered lookup and facts rows; results retain input order.
    facts = [
        {"amfi_scheme_code": 20, "aum": Decimal("17.23"),
         "base_expense_ratio": Decimal("1.4"), "return_5year": Decimal("12.1"),
         "benchmark_name": "Index TRI", "inception_date": date(2001, 1, 1)},
        {"amfi_scheme_code": 10, "aum": Decimal("0")},
    ]
    conn = _Connection([_fund(10, "INF10"), _fund(20, "INF20")], facts)
    original_facts = [dict(row) for row in facts]
    result = queries.funds_batch(conn, ids)
    assert len(conn.calls) == 2
    assert result["requested"] == len(set(ids))
    assert result["found"] == 2
    assert result["not_found"] == ["missing"]
    assert [row["amfi_scheme_code"] for row in result["funds"]] == [20, 10]
    first, second = result["funds"]
    assert first["matched_by"] == list(dict.fromkeys(i for i in ids if i in {"INF20", "20"}))
    assert first["aum"] == 17.23
    assert first["base_expense_ratio"] == 1.4
    assert first["return_5year"] == 12.1
    assert first["benchmark_name"] == "Index TRI"
    assert first["inception_date"] == "2001-01-01"
    assert first["latest_nav"] == 101.25
    assert first["latest_nav_date"] == "2026-09-30"
    assert first["facts_source_code"] is None
    assert second["aum"] == 0.0
    assert second["fund_manager_name"] is None
    assert "return_5year" not in second
    assert facts == original_facts


@pytest.mark.parametrize("ids", [[], ["999", "bad", "bad"]])
def test_no_matches_never_evaluates_the_facts_graph(ids):
    conn = _Connection([], [])
    result = queries.funds_batch(conn, ids)
    assert len(conn.calls) == 1
    assert result == {"requested": len(set(ids)), "found": 0,
                      "not_found": list(dict.fromkeys(ids)), "funds": []}


def test_only_resolved_codes_are_evaluated_for_shared_historical_isin():
    # Keep the existing first-match resolution; unrelated candidate rows must
    # not enlarge the facts work beyond the resolved request cohort.
    conn = _Connection([_fund(20, "SHARED"), _fund(30, "SHARED")],
                       [{"amfi_scheme_code": 20, "aum": Decimal("1")}],
                       expected_codes=[20])
    result = queries.funds_batch(conn, ["SHARED"])
    assert result["found"] == 1
    assert result["funds"][0]["amfi_scheme_code"] == 20
    assert result["funds"][0]["matched_by"] == ["SHARED"]
    assert len(conn.calls) == 2


def test_fifty_resolved_funds_still_use_one_facts_statement():
    conn = _Connection([_fund(code, f"INF{code}") for code in range(1, 51)],
                       [{"amfi_scheme_code": code, "aum": Decimal(code)} for code in range(1, 51)])
    result = queries.funds_batch(conn, [str(code) for code in range(50, 0, -1)])
    assert result["found"] == 50
    assert [f["amfi_scheme_code"] for f in result["funds"]] == list(range(50, 0, -1))
    assert len(conn.calls) == 2


@pytest.mark.parametrize("family_only", [False, True])
def test_empty_scalar_cohort_never_queries_the_view(family_only):
    class Forbidden:
        def execute(self, *_args):
            raise AssertionError("empty facts cohort must not execute SQL")
    assert queries._batch_current_facts(Forbidden(), [], family_only=family_only) == []


def test_family_projection_is_scalar_scoped_and_excludes_performance():
    class Capture:
        def execute(self, query, params):
            assert "CROSS JOIN LATERAL" in query
            assert "WHERE amfi_scheme_code = requested.code OFFSET 0" in query
            assert params == {"codes": [10, 20]}
            for key in ("return_5year", "sharpe_ratio", "beta"):
                assert key not in query
            assert "COALESCE(benchmark_name, benchmark) AS benchmark_name" in query
            return _Rows([])
    assert queries._batch_current_facts(Capture(), [20, 10, 20], family_only=True) == []


def test_sibling_selection_cannot_flatten_into_a_global_facts_join():
    query = queries._BATCH_SIBLING_SOURCE_SQL
    assert "JOIN LATERAL" in query
    assert "WHERE amfi_scheme_code = v2.amfi_scheme_code OFFSET 0" in query
    assert "PARTITION BY v1.amfi_scheme_code" in query
    assert "f.periodicity IS NOT DISTINCT FROM f1.periodicity" in query
