"""Integration tests for variant-group facts fallback (additive, self-cleaning).

Unlike ``test_postgres_integration.py`` these tests never wipe the database:
they insert a small synthetic fund family under the 900xxx AMFI code range
and delete exactly those rows afterwards, so they are safe to run against a
populated database. Skipped unless ``MF_TEST_DSN`` is set.

Covers:
  * exact-code facts are returned unchanged (facts_source_code is None);
  * a code with no facts of its own borrows the group's canonical variant
    (GROWTH/REGULAR, lowest AMFI code) and discloses facts_source_code;
  * a facts row that is all-NULL is treated as "no facts";
  * codes with no variant group and no facts stay blank (graceful);
  * /api/funds/batch applies the same fallback as the detail endpoint.
"""

from __future__ import annotations

import pytest

from mfdataindia.api import queries

pytestmark = pytest.mark.postgres

_TEST_AMC_ID = 900000
_TEST_CODES = (900001, 900002, 900003, 900004, 900005, 900006, 900007, 900008, 900009)
_GROUP1 = "VFALLBACK-G1"
_GROUP2 = "VFALLBACK-G2"


def _insert_fixture(cur) -> None:
    cur.execute(
        """
        INSERT INTO mf.amcs (amc_id, amfi_amc_name, normalised_name)
        OVERRIDING SYSTEM VALUE
        VALUES (%s, 'Variant Fallback Test AMC', 'variant fallback test amc')
        ON CONFLICT (amc_id) DO NOTHING
        """,
        (_TEST_AMC_ID,),
    )
    funds = [
        # (code, name, plan, option, group)
        (900001, "Variant Fallback Family Fund - Regular Plan - Growth", "REGULAR", "GROWTH", _GROUP1),
        (900002, "Variant Fallback Family Fund - Regular Plan - IDCW", "REGULAR", "IDCW", _GROUP1),
        (900003, "Variant Fallback Family Fund - Direct Plan - Growth", "DIRECT", "GROWTH", _GROUP1),
        (900004, "Variant Fallback Family Fund - Regular Plan - Growth (re-issue)",
         "REGULAR", "GROWTH", _GROUP1),
        (900009, "Variant Fallback Family Fund - Regular Plan - Growth (2nd re-issue)",
         "REGULAR", "GROWTH", _GROUP1),
        (900005, "Variant Fallback Standalone No Facts - Regular Plan - Growth",
         "REGULAR", "GROWTH", None),
        (900006, "Variant Fallback Standalone With Facts - Regular Plan - Growth",
         "REGULAR", "GROWTH", None),
        (900007, "Variant Fallback Unusable Facts - Regular Plan - Growth",
         "REGULAR", "GROWTH", _GROUP2),
        (900008, "Variant Fallback Unusable Facts - Regular Plan - IDCW",
         "REGULAR", "IDCW", _GROUP2),
    ]
    for code, name, plan, option, group in funds:
        cur.execute(
            """
            INSERT INTO mf.funds
                (amfi_scheme_code, scheme_name, scheme_name_norm, amc_id, scheme_type,
                 scheme_category, plan_type, option_type)
            VALUES (%s, %s, %s, %s, 'OPEN_ENDED',
                    'Debt Scheme - Liquid Fund', %s, %s)
            ON CONFLICT (amfi_scheme_code) DO NOTHING
            """,
            (code, name, name.upper(), _TEST_AMC_ID, plan, option),
        )
        if group:
            cur.execute(
                """
                INSERT INTO mf.fund_variants (amfi_scheme_code, group_key, base_scheme_name)
                VALUES (%s, %s, 'Variant Fallback Family Fund')
                ON CONFLICT (amfi_scheme_code) DO NOTHING
                """,
                (code, group),
            )
    # G1: facts on 900002 (REG/IDCW), 900003 (DIRECT/GROWTH), 900004 + 900009
    # (REG/GROWTH). 900001 has none -> canonical source must be 900004
    # (GROWTH beats IDCW/DIRECT, lowest code among REG/GROWTH).
    # G2: 900007 has a facts row but every field NULL (unusable) -> 900008.
    for code, aum in ((900002, "111"), (900003, "222"), (900004, "333"),
                      (900006, "444"), (900008, "555"), (900009, "666")):
        cur.execute(
            """
            INSERT INTO mf.fund_facts
                (amfi_scheme_code, aum, expense_ratio, benchmark_name,
                 fund_manager_name, inception_date, source)
            VALUES (%s, %s, 0.25, 'TEST BENCHMARK', 'Test Manager', DATE '2001-01-01', 'AMC')
            ON CONFLICT (amfi_scheme_code) DO NOTHING
            """,
            (code, aum),
        )
    cur.execute(
        "INSERT INTO mf.fund_facts (amfi_scheme_code, source) VALUES (900007, 'AMC')"
        " ON CONFLICT (amfi_scheme_code) DO NOTHING"
    )


def _cleanup(cur) -> None:
    codes = list(_TEST_CODES)  # psycopg v3 adapts lists to PG arrays, not tuples
    for table in ("fund_facts", "fund_variants", "nav_history", "funds"):
        cur.execute(f"DELETE FROM mf.{table} WHERE amfi_scheme_code = ANY(%s::int[])",
                    (codes,))
    cur.execute("DELETE FROM mf.amcs WHERE amc_id = %s", (_TEST_AMC_ID,))


@pytest.fixture
def fallback_db(pg_dsn):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    conn = psycopg.connect(pg_dsn, row_factory=dict_row, autocommit=True)
    cur = conn.cursor()
    _cleanup(cur)  # remove leftovers from an earlier crashed run
    _insert_fixture(cur)
    yield conn
    _cleanup(cur)
    conn.close()


def test_exact_code_facts_unchanged(fallback_db):
    d = queries.fund_detail(fallback_db, 900006)
    assert d is not None
    assert float(d["facts"]["aum"]) == 444.0
    assert d["facts_source_code"] is None


def test_no_facts_resolves_sibling_canonical_variant(fallback_db):
    d = queries.fund_detail(fallback_db, 900001)
    assert d is not None
    assert d["facts"] is not None
    assert float(d["facts"]["aum"]) == 333.0  # from 900004, not 111/222/666
    assert d["facts"]["fund_manager_name"] == "Test Manager"
    assert d["facts_source_code"] == 900004


def test_all_null_facts_row_treated_as_missing(fallback_db):
    d = queries.fund_detail(fallback_db, 900007)
    assert d is not None
    assert d["facts"] is not None
    assert float(d["facts"]["aum"]) == 555.0
    assert d["facts_source_code"] == 900008


def test_no_group_and_no_facts_stays_blank(fallback_db):
    d = queries.fund_detail(fallback_db, 900005)
    assert d is not None
    assert d["facts"] is None
    assert d["facts_source_code"] is None


def test_batch_falls_back_like_detail(fallback_db):
    res = queries.funds_batch(fallback_db, ["900001", "900006"])
    by_code = {f["amfi_scheme_code"]: f for f in res["funds"]}
    assert res["found"] == 2

    borrowed = by_code[900001]
    assert float(borrowed["aum"]) == 333.0
    assert borrowed["fund_manager_name"] == "Test Manager"
    assert borrowed["facts_source_code"] == 900004

    exact = by_code[900006]
    assert float(exact["aum"]) == 444.0
    assert exact["facts_source_code"] is None


def test_batch_no_group_no_facts_stays_blank(fallback_db):
    res = queries.funds_batch(fallback_db, ["900005"])
    assert res["found"] == 1
    f = res["funds"][0]
    assert "aum" not in f
    assert f["facts_source_code"] is None