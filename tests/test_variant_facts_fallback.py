"""Integration tests for the variant-group facts fallback.

Safe to run against a populated database: the synthetic fund family (900xxx
AMFI codes, AMC 900000) is inserted inside a single transaction that is always
rolled back, so nothing is ever committed. If any of the synthetic identifiers
already exist in the target database the fixture fails loudly instead of
touching real rows. Skipped unless ``MF_TEST_DSN`` is set.

Covers:
  * exact-code facts are returned unchanged (facts_source_code is None);
  * a code with no facts of its own borrows a same plan/option/periodicity
    re-issue's facts (lowest AMFI code) and discloses facts_source_code;
  * borrowing is restricted to the same plan/option/periodicity within the same
    AMC and scheme category — a Direct/IDCW scheme never exposes Regular/Growth
    values, and a Monthly IDCW never borrows a Quarterly sibling's facts;
  * only family-safe fields are borrowed — per-scheme fields (SIP, transaction
    status, exit load, published returns, risk ratios) are never inherited;
  * a facts row that is all-NULL is treated as "no facts";
  * a sibling usable via expense_ratio alone still qualifies;
  * the borrowing code's own per-code facts (e.g. Sharpe) survive the borrow;
  * codes with no variant group and no facts stay blank (graceful);
  * /api/funds/batch applies the same fallback as the detail endpoint;
  * the family view keeps same-named funds from different AMCs as separate
    families (group_key alone is a heuristic).
"""

from __future__ import annotations

from datetime import date

import pytest

from mfdataindia.api import queries

pytestmark = pytest.mark.postgres

_TEST_AMC_ID = 900000
_TEST_AMC_ID_2 = 900001
_TEST_CODES = (
    900001, 900002, 900003, 900004, 900009,
    900005, 900006,
    900007, 900008,
    900010, 900011, 900012, 900013,
    900030, 900031,
    900040, 900041,
    900050, 900051,
    900060, 900061, 900062,
    900070, 900071,
)
_CAT = "Debt Scheme - Liquid Fund"
_INCEPTION = date(2001, 1, 1)


def _insert_fixture(cur) -> None:
    """Insert the synthetic families. Caller owns the transaction."""
    cur.execute(
        """
        INSERT INTO mf.amcs (amc_id, amfi_amc_name, normalised_name)
        OVERRIDING SYSTEM VALUE
        VALUES (%s, 'Variant Fallback Test AMC', 'variant fallback test amc')
        """,
        (_TEST_AMC_ID,),
    )
    # Second AMC for the cross-AMC family-collision test (G8).
    cur.execute(
        """
        INSERT INTO mf.amcs (amc_id, amfi_amc_name, normalised_name)
        OVERRIDING SYSTEM VALUE
        VALUES (%s, 'Variant Fallback Test AMC Two', 'variant fallback test amc two')
        """,
        (_TEST_AMC_ID_2,),
    )
    funds = [
        # (code, name, plan, option, group)
        # G1: 900001 (REG/GROWTH, no facts) must borrow 900004 — the lowest
        # REG/GROWTH re-issue with facts. 900002 (REG/IDCW) and 900003
        # (DIRECT/GROWTH) have *lower* codes and usable facts but the wrong
        # plan/option, so they must be filtered out; 900009 is a higher
        # REG/GROWTH re-issue.
        (900001, "Variant Fallback G1 - Regular Plan - Growth",
         "REGULAR", "GROWTH", "VFALLBACK-G1"),
        (900002, "Variant Fallback G1 - Regular Plan - IDCW",
         "REGULAR", "IDCW", "VFALLBACK-G1"),
        (900003, "Variant Fallback G1 - Direct Plan - Growth",
         "DIRECT", "GROWTH", "VFALLBACK-G1"),
        (900004, "Variant Fallback G1 - Regular Plan - Growth (re-issue)",
         "REGULAR", "GROWTH", "VFALLBACK-G1"),
        (900009, "Variant Fallback G1 - Regular Plan - Growth (2nd re-issue)",
         "REGULAR", "GROWTH", "VFALLBACK-G1"),
        # Standalone: no variant group.
        (900005, "Variant Fallback Standalone No Facts - Regular Plan - Growth",
         "REGULAR", "GROWTH", None),
        (900006, "Variant Fallback Standalone With Facts - Regular Plan - Growth",
         "REGULAR", "GROWTH", None),
        # G2: 900007's facts row is all-NULL (unusable) -> borrow 900008, a
        # same plan/option re-issue.
        (900007, "Variant Fallback G2 - Regular Plan - Growth",
         "REGULAR", "GROWTH", "VFALLBACK-G2"),
        (900008, "Variant Fallback G2 - Regular Plan - Growth (re-issue)",
         "REGULAR", "GROWTH", "VFALLBACK-G2"),
        # G3: cross-plan isolation. 900010 (DIRECT/GROWTH, no facts) must
        # borrow 900012 (DIRECT/GROWTH) — never 900011 (REGULAR/GROWTH, a
        # *lower* code with facts) or 900013 (DIRECT/IDCW).
        (900010, "Variant Fallback G3 - Direct Plan - Growth",
         "DIRECT", "GROWTH", "VFALLBACK-G3"),
        (900011, "Variant Fallback G3 - Regular Plan - Growth",
         "REGULAR", "GROWTH", "VFALLBACK-G3"),
        (900012, "Variant Fallback G3 - Direct Plan - Growth (re-issue)",
         "DIRECT", "GROWTH", "VFALLBACK-G3"),
        (900013, "Variant Fallback G3 - Direct Plan - IDCW",
         "DIRECT", "IDCW", "VFALLBACK-G3"),
        # G4: per-code override. 900031 (no identity facts, own sharpe) borrows
        # 900030's fund-level facts but keeps its own sharpe.
        (900030, "Variant Fallback G4 - Regular Plan - Growth",
         "REGULAR", "GROWTH", "VFALLBACK-G4"),
        (900031, "Variant Fallback G4 - Regular Plan - Growth (re-issue)",
         "REGULAR", "GROWTH", "VFALLBACK-G4"),
        # G5: 900040 is usable via expense_ratio alone; 900041 borrows it.
        (900040, "Variant Fallback G5 - Regular Plan - Growth",
         "REGULAR", "GROWTH", "VFALLBACK-G5"),
        (900041, "Variant Fallback G5 - Regular Plan - Growth (re-issue)",
         "REGULAR", "GROWTH", "VFALLBACK-G5"),
    ]
    for code, name, plan, option, group in funds:
        cur.execute(
            """
            INSERT INTO mf.funds
                (amfi_scheme_code, scheme_name, scheme_name_norm, amc_id,
                 scheme_type, scheme_category, plan_type, option_type)
            VALUES (%s, %s, %s, %s, 'OPEN_ENDED', %s, %s, %s)
            """,
            (code, name, name.upper(), _TEST_AMC_ID, _CAT, plan, option),
        )
        if group:
            cur.execute(
                """
                INSERT INTO mf.fund_variants
                    (amfi_scheme_code, group_key, base_scheme_name)
                VALUES (%s, %s, %s)
                """,
                (code, group, name.split(" - ")[0]),
            )
    # Full fund-level facts (aum + er + benchmark + manager + inception).
    full = (
        "INSERT INTO mf.fund_facts"
        " (amfi_scheme_code, aum, expense_ratio, benchmark, benchmark_name,"
        "  fund_manager_name, inception_date, source)"
        " VALUES (%s, %s, 0.25, 'TEST BENCH', 'TEST BENCHMARK', 'Test Manager',"
        "  %s, 'AMC')"
    )
    for code, aum in ((900004, "333"), (900006, "444"), (900008, "555"),
                      (900012, "888"), (900030, "50"),
                      (900011, "777"), (900013, "999"),
                      (900002, "111"), (900003, "222"), (900009, "666")):
        cur.execute(full, (code, aum, _INCEPTION))
    # G2: 900007 has a facts row but every identity field is NULL (unusable).
    cur.execute(
        "INSERT INTO mf.fund_facts (amfi_scheme_code, source) VALUES (%s, 'AMC')",
        (900007,),
    )
    # G4: 900031 has only a per-code Sharpe — not an identity field, so the
    # row is "unusable" and the code falls back, but its sharpe must survive.
    cur.execute(
        "INSERT INTO mf.fund_facts (amfi_scheme_code, sharpe_ratio, source)"
        " VALUES (%s, 3.3, 'AMC')",
        (900031,),
    )
    # G5: 900040 is usable via expense_ratio alone (no aum/bench/manager/inc).
    cur.execute(
        "INSERT INTO mf.fund_facts (amfi_scheme_code, expense_ratio, source)"
        " VALUES (%s, 0.9, 'AMC')",
        (900040,),
    )

    # G6: code-specific isolation. 900051 (REG/GROWTH) carries BOTH family-safe
    # and code-specific facts; 900050 (REG/GROWTH, no facts) borrows only the
    # family-safe subset and must NOT inherit 900051's sharpe / SIP / exit load
    # / transaction status.
    for code, name in ((900050, "Variant Fallback G6 - Regular Plan - Growth"),
                       (900051, "Variant Fallback G6 - Regular Plan - Growth (re-issue)")):
        cur.execute(
            """
            INSERT INTO mf.funds
                (amfi_scheme_code, scheme_name, scheme_name_norm, amc_id, scheme_type,
                 scheme_category, plan_type, option_type)
            VALUES (%s, %s, %s, %s, 'OPEN_ENDED', %s, 'REGULAR', 'GROWTH')
            """,
            (code, name, name.upper(), _TEST_AMC_ID, _CAT),
        )
        cur.execute(
            "INSERT INTO mf.fund_variants (amfi_scheme_code, group_key, base_scheme_name)"
            " VALUES (%s, 'VFALLBACK-G6', %s)",
            (code, name.split(" - ")[0]),
        )
    cur.execute(full, (900051, "77", _INCEPTION))
    # 900051's code-specific fields (deliberately outside the family-safe list).
    cur.execute(
        """
        UPDATE mf.fund_facts
        SET sharpe_ratio = 2.0, is_sip_allowed = true, exit_load_value = 1.5,
            transaction_status = 'ACTIVE'
        WHERE amfi_scheme_code = %s
        """,
        (900051,),
    )

    # G7: IDCW periodicity isolation. 900060 (MONTHLY, no facts) must borrow
    # 900062 (MONTHLY), not the lower-code 900061 (QUARTERLY).
    for code, name, period in (
        (900060, "Variant Fallback G7 - Regular Plan - IDCW", "MONTHLY"),
        (900061, "Variant Fallback G7 - Regular Plan - IDCW (quarterly)", "QUARTERLY"),
        (900062, "Variant Fallback G7 - Regular Plan - IDCW (monthly re-issue)", "MONTHLY"),
    ):
        cur.execute(
            """
            INSERT INTO mf.funds
                (amfi_scheme_code, scheme_name, scheme_name_norm, amc_id, scheme_type,
                 scheme_category, plan_type, option_type, periodicity)
            VALUES (%s, %s, %s, %s, 'OPEN_ENDED', %s, 'REGULAR', 'IDCW', %s)
            """,
            (code, name, name.upper(), _TEST_AMC_ID, _CAT, period),
        )
        cur.execute(
            "INSERT INTO mf.fund_variants (amfi_scheme_code, group_key, base_scheme_name)"
            " VALUES (%s, 'VFALLBACK-G7', %s)",
            (code, name.split(" - ")[0]),
        )
    cur.execute(full, (900061, "11", _INCEPTION))
    cur.execute(full, (900062, "22", _INCEPTION))

    # G8: cross-AMC family collision. 900070 (AMC 900000) and 900071 (AMC 900001)
    # share a base name -> the same group_key, but different AMCs. They must stay
    # two separate families in list_fund_families rather than merge into one.
    for code, name, amc in (
        (900070, "Variant Fallback G8 - Regular Plan - Growth", _TEST_AMC_ID),
        (900071, "Variant Fallback G8 - Regular Plan - Growth", _TEST_AMC_ID_2),
    ):
        cur.execute(
            """
            INSERT INTO mf.funds
                (amfi_scheme_code, scheme_name, scheme_name_norm, amc_id, scheme_type,
                 scheme_category, plan_type, option_type)
            VALUES (%s, %s, %s, %s, 'OPEN_ENDED', %s, 'REGULAR', 'GROWTH')
            """,
            (code, name, name.upper(), amc, _CAT),
        )
        cur.execute(
            "INSERT INTO mf.fund_variants (amfi_scheme_code, group_key, base_scheme_name)"
            " VALUES (%s, 'VFALLBACK-G8', %s)",
            (code, name.split(" - ")[0]),
        )


@pytest.fixture
def fallback_db(pg_dsn):
    """Open a connection, insert the family in a single transaction, and
    guarantee it is rolled back. Fails (rather than deleting) if any synthetic
    identifier already exists, so a populated database is never damaged.
    """
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    conn = psycopg.connect(pg_dsn, row_factory=dict_row, autocommit=False)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT amfi_scheme_code FROM mf.funds "
            "WHERE amfi_scheme_code = ANY(%s::int[])",
            (list(_TEST_CODES),),
        )
        preexisting = [r["amfi_scheme_code"] for r in cur.fetchall()]
        cur.execute(
            "SELECT amc_id FROM mf.amcs WHERE amc_id = ANY(%s::int[])",
            ([_TEST_AMC_ID, _TEST_AMC_ID_2],),
        )
        amc_preexisting = [r["amc_id"] for r in cur.fetchall()]
        if preexisting or amc_preexisting:
            conn.rollback()
            pytest.fail(
                "synthetic identifiers already exist "
                f"(funds: {preexisting}, amcs: {amc_preexisting}); "
                "refusing to run against a database containing these codes"
            )
        _insert_fixture(cur)
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_exact_code_facts_unchanged(fallback_db):
    d = queries.fund_detail(fallback_db, 900006)
    assert d is not None
    assert float(d["facts"]["aum"]) == 444.0
    assert d["facts_source_code"] is None


def test_no_facts_borrows_same_plan_option_reissue(fallback_db):
    # 900001 (REG/GROWTH) has no facts. The lowest *same plan/option* re-issue
    # with facts is 900004 — even though 900002 (REG/IDCW) and 900003
    # (DIRECT/GROWTH) have lower codes and facts, they are the wrong
    # plan/option and must not be chosen.
    d = queries.fund_detail(fallback_db, 900001)
    assert d is not None
    assert d["facts"] is not None
    assert float(d["facts"]["aum"]) == 333.0  # from 900004, not 111/222/666
    assert d["facts"]["fund_manager_name"] == "Test Manager"
    assert d["facts_source_code"] == 900004


def test_direct_scheme_never_borrows_regular(fallback_db):
    # P1: a Direct/Growth scheme must not expose a Regular/Growth sibling's
    # expense ratio or other plan-specific facts. 900011 (REGULAR/GROWTH) has
    # a lower code and usable facts but a different plan; 900013 (DIRECT/IDCW)
    # is a different option. Only 900012 (DIRECT/GROWTH) qualifies.
    d = queries.fund_detail(fallback_db, 900010)
    assert d is not None
    assert d["facts"] is not None
    assert d["facts_source_code"] == 900012
    assert float(d["facts"]["aum"]) == 888.0
def test_all_null_facts_row_treated_as_missing(fallback_db):
    # 900007 has a facts row but every identity field is NULL -> unusable, so
    # it borrows its same plan/option re-issue 900008.
    d = queries.fund_detail(fallback_db, 900007)
    assert d is not None
    assert d["facts"] is not None
    assert float(d["facts"]["aum"]) == 555.0
    assert d["facts_source_code"] == 900008


def test_sibling_usable_via_expense_ratio_alone(fallback_db):
    # 900040 has only expense_ratio set (no aum/benchmark/manager/inception).
    # It still counts as having its own facts, so 900041 borrows from it.
    d = queries.fund_detail(fallback_db, 900041)
    assert d is not None
    assert d["facts"] is not None
    assert d["facts_source_code"] == 900040
    assert d["facts"]["aum"] is None
    assert float(d["facts"]["expense_ratio"]) == 0.9


def test_own_per_code_facts_survive_borrow(fallback_db):
    # 900031 has only its own Sharpe (not an identity field) so it falls back
    # to 900030 for fund-level facts, but its own sharpe must be preserved.
    d = queries.fund_detail(fallback_db, 900031)
    assert d is not None
    assert d["facts_source_code"] == 900030
    assert float(d["facts"]["aum"]) == 50.0         # borrowed from 900030
    assert float(d["facts"]["sharpe_ratio"]) == 3.3  # its own, not 900030's


def test_code_specific_fields_not_borrowed(fallback_db):
    # 900050 has no facts and borrows from 900051. It must inherit the
    # family-safe fields (aum, benchmark, manager, inception) but NOT the
    # per-scheme fields 900051 carries (sharpe, SIP, exit load, txn status).
    d = queries.fund_detail(fallback_db, 900050)
    assert d is not None
    assert d["facts_source_code"] == 900051
    f = d["facts"]
    # family-safe fields ARE borrowed
    assert float(f["aum"]) == 77.0
    assert f["benchmark_name"] == "TEST BENCHMARK"
    assert f["fund_manager_name"] == "Test Manager"
    assert f["inception_date"] == _INCEPTION.isoformat()
    # code-specific fields are NOT inherited (stay null)
    assert f.get("sharpe_ratio") is None
    assert f.get("is_sip_allowed") is None
    assert f.get("exit_load_value") is None
    assert f.get("transaction_status") is None


def test_periodicity_isolates_idcw_variants_detail(fallback_db):
    # 900060 (MONTHLY, no facts) must borrow the MONTHLY re-issue 900062, not
    # the lower-code QUARTERLY 900061.
    d = queries.fund_detail(fallback_db, 900060)
    assert d is not None
    assert d["facts_source_code"] == 900062
    assert float(d["facts"]["aum"]) == 22.0


def test_periodicity_isolates_idcw_variants_batch(fallback_db):
    res = queries.funds_batch(fallback_db, ["900060"])
    f = res["funds"][0]
    assert f["facts_source_code"] == 900062
    assert float(f["aum"]) == 22.0


def test_cross_amc_same_name_stays_separate_families(fallback_db):
    # 900070 and 900071 share a base name (same group_key) but belong to
    # different AMCs; the family view must keep them as two families, not merge
    # them into one and inflate a single variant_count.
    res = queries.list_fund_families(fallback_db, q="Variant Fallback G8")
    assert res["total"] == 2
    codes = {r["amfi_scheme_code"] for r in res["results"]}
    assert codes == {900070, 900071}
    for r in res["results"]:
        assert r["variant_count"] == 1


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


def test_batch_cross_plan_isolation_matches_detail(fallback_db):
    # The batch endpoint must apply the same same-plan/option restriction:
    # 900010 (DIRECT/GROWTH) borrows 900012, not the lower-code 900011.
    res = queries.funds_batch(fallback_db, ["900010"])
    f = res["funds"][0]
    assert f["facts_source_code"] == 900012
    assert float(f["aum"]) == 888.0


def test_batch_no_group_no_facts_stays_blank(fallback_db):
    res = queries.funds_batch(fallback_db, ["900005"])
    assert res["found"] == 1
    f = res["funds"][0]
    assert "aum" not in f
    assert f["facts_source_code"] is None


def test_rollback_leaves_no_rows(pg_dsn):
    # After every fixture transaction rolls back, no synthetic rows persist —
    # the suite leaves the database exactly as it found it.
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(pg_dsn, autocommit=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM mf.funds WHERE amfi_scheme_code = ANY(%s::int[])",
            (list(_TEST_CODES),),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM mf.amcs WHERE amc_id = ANY(%s::int[])",
            ([_TEST_AMC_ID, _TEST_AMC_ID_2],),
        )
        assert cur.fetchone()[0] == 0
    finally:
        conn.close()