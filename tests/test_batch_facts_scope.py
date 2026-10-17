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
    def __init__(self, funds, facts):
        self.funds = funds
        self.facts = facts
        self.calls = []

    def execute(self, query, params):
        self.calls.append((query, params))
        if len(self.calls) == 1:
            # No unconstrained facts join in the mixed code/ISIN lookup.
            assert "mf.current_fund_facts" not in query
            return _Rows(self.funds)
        assert len(self.calls) == 2
        assert "FROM mf.current_fund_facts" in query
        assert "WHERE amfi_scheme_code = ANY(%(codes)s::int[])" in query
        assert params == {"codes": sorted({r["amfi_scheme_code"] for r in self.funds})}
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
