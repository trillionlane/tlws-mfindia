"""Page identity must bound derived-facts work without changing global sorts."""
from datetime import date
from decimal import Decimal

import pytest

from mfdataindia.api import queries


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return {"n": 42}

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, dict(params)))
        return Rows([])


@pytest.mark.parametrize("sort", ["name", "unknown", "nav", "aum", "expense", "return_5y"])
def test_fund_page_has_scalar_facts_join_and_preserves_sort_scope(sort):
    conn = Connection()
    result = queries.list_funds(conn, sort=sort, page=3, per_page=2, q="SBI", amc="AMC", category="CAT", option="IDCW", live=False, in_scope=False)
    assert result == {"total": 42, "page": 3, "per_page": 2, "results": []}
    count, page = [sql for sql, _ in conn.calls]
    assert "current_fund_facts" not in count
    assert page.index("ORDER BY pos LIMIT %(per)s OFFSET %(off)s") < page.index("WHERE amfi_scheme_code = p.amfi_scheme_code")
    assert "SELECT aum, expense_ratio, return_5year" in page
    assert "OFFSET 0\n        ) ff ON true" in page
    assert "LEFT JOIN mf.current_fund_facts ff ON ff.amfi_scheme_code = p." not in page
    if sort in ("aum", "expense", "return_5y"):
        assert page.index("LEFT JOIN mf.current_fund_facts fs") < page.index("ORDER BY pos LIMIT")
    assert conn.calls[1][1] == {"q": "%SBI%", "qeq": "SBI", "amc": "AMC", "cat": "CAT", "opt": "IDCW", "per": 2, "off": 4}


@pytest.mark.parametrize("sort", ["name", "unknown", "nav", "aum", "expense", "return_5y"])
def test_family_name_page_only_precedes_facts_when_sort_is_identity_only(sort):
    conn = Connection()
    result = queries.list_fund_families(conn, sort=sort, page=3, per_page=2, q="SBI", live=False)
    assert result == {"total": 42, "page": 3, "per_page": 2, "results": []}
    count, page = [sql for sql, _ in conn.calls]
    assert "current_fund_facts" not in count
    assert "PARTITION BY fam_key, amc_id, scheme_type, scheme_category" in page
    assert "WHERE rn = 1" in page
    assert "f.in_scope" in page and "f.plan_type = 'REGULAR'" in page
    if sort in ("name", "unknown"):
        assert page.index("LIMIT %(per)s OFFSET %(off)s") < page.index("FROM mf.current_fund_facts")
        assert "WHERE amfi_scheme_code = s.amfi_scheme_code" in page
        assert "OFFSET 0\n            ) ff ON true" in page
    else:
        assert page.index("LEFT JOIN mf.current_fund_facts ff") < page.index("LIMIT %(per)s OFFSET %(off)s")
        assert "WHERE amfi_scheme_code = s.amfi_scheme_code" not in page
    assert page.count("LIMIT %(per)s OFFSET %(off)s") == 1
    assert conn.calls[1][1] == {"q": "%SBI%", "qeq": "SBI", "per": 2, "off": 4}


def test_page_serialization_preserves_zero_null_and_dates():
    row = {"amfi_scheme_code": 1, "latest_nav": Decimal("0"), "latest_nav_date": date(2026, 9, 30), "aum": Decimal("0"), "expense_ratio": None, "return_5year": Decimal("-2.5")}
    class Scalar(Connection):
        def execute(self, sql, params):
            self.calls.append((sql, dict(params)))
            return Rows([dict(row)])
    for function in (queries.list_funds, queries.list_fund_families):
        out = function(Scalar(), per_page=1)["results"][0]
        assert out == {**row, "latest_nav": 0.0, "latest_nav_date": "2026-09-30", "aum": 0.0, "return_5year": -2.5}


def test_risk_map_materializes_current_category_and_eligible_peers_before_aum():
    class Risk(Connection):
        def execute(self, sql, params):
            self.calls.append((sql, dict(params)))
            if len(self.calls) == 1:
                class Subject:
                    def fetchone(self):
                        return {"amfi_scheme_code": 1, "scheme_name": "Fund", "sebi_category_name": "Current category", "aum": None}
                return Subject()
            return Rows([])
    conn = Risk()
    assert queries.risk_reward(conn, 1) == {"code": 1, "category": "Current category", "points": [], "refreshed": False}
    sql, params = conn.calls[1]
    assert "WITH peers AS MATERIALIZED" in sql
    assert "JOIN mf.current_fund_facts category" in sql
    assert "category.sebi_category_name = %(cat)s" in sql
    assert "mf.fund_facts" not in sql  # No bypass to stale, unoverlaid categories.
    assert "f.in_scope AND NOT f.is_defunct" in sql
    assert queries._plan_predicate() in sql
    assert queries._comparison_eligible_predicate("f") in sql
    assert sql.index("FROM peers") < sql.index("WHERE amfi_scheme_code = peers.amfi_scheme_code")
    assert "OFFSET 0\n        ) ff ON true" in sql
    assert "ORDER BY peers.annual_vol" in sql
    assert params == {"cat": "Current category"}
