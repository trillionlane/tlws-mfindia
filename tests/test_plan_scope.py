"""Plan-scope tests: the served universe is Regular, never Direct.

Safe to run against a populated database: the synthetic scheme group (9002xx
AMFI codes, AMC 900002) is inserted inside a single transaction that is always
rolled back. If any synthetic identifier already exists the fixture fails loudly
instead of touching real rows. Skipped unless ``MF_TEST_DSN`` is set.

``mf.funds.in_scope`` is a STORED generated column that already excludes Direct,
so most tests deliberately pass ``in_scope=False`` (or use the sibling
navigation list, which never applies ``in_scope``). That is what proves the plan
predicate does real work on its own instead of riding along with curation.

Also safe on an untouched database: the fixture migrates when ``mf.funds`` does
not exist yet — CI's Postgres service starts empty and this file is collected
*before* ``test_postgres_integration.py``, so it cannot inherit a schema from it
— and the one test that only means something against real data reports a skip
rather than passing vacuously on an empty one.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from mfdataindia.api import queries

pytestmark = pytest.mark.postgres

_TEST_AMC_ID = 900002
_TEST_CODES = tuple(range(900200, 900210))
_CAT = "Debt Scheme - Liquid Fund"
_GROUP = "PLANSCOPE-G1"
_NAME = "Plan Scope Test Fund"
# A valid 12-char ISIN that no query here matches. Its only job is to make
# isin_primary non-NULL on ONE row so the NULLS FIRST regression is observable.
_ISIN = "PS0TEST0000A"


def _insert_fixture(cur) -> None:
    """Insert one synthetic variant group spanning every plan label."""
    cur.execute(
        """
        INSERT INTO mf.amcs (amc_id, amfi_amc_name, normalised_name)
        OVERRIDING SYSTEM VALUE
        VALUES (%s, 'Plan Scope Test AMC', 'plan scope test amc')
        """,
        (_TEST_AMC_ID,),
    )
    # (code, plan, option, periodicity, plan_source, isin)
    # in_scope is GENERATED: Regular -> true; Unlabelled -> true only when
    # plan_source = 'NAME'; Direct / Retail -> false.
    funds = [
        (900200, "REGULAR", "GROWTH", None, "COLUMN", _ISIN),
        (900201, "REGULAR", "IDCW", "QUARTERLY", "COLUMN", None),
        (900202, "DIRECT", "GROWTH", None, "COLUMN", None),
        (900203, "DIRECT", "IDCW", "QUARTERLY", "COLUMN", None),
        (900204, "UNLABELLED", "GROWTH", None, "NAME", None),
        (900205, "UNLABELLED", "GROWTH", None, "COLUMN_BLANK", None),
        (900206, "RETAIL", "GROWTH", None, "COLUMN", None),
        (900207, "REGULAR", "GROWTH", None, "COLUMN", None),
    ]
    for index, (code, plan, option, period, plan_source, isin) in enumerate(funds):
        # Names sort A..H, so both the code tie-break and the NULLS FIRST probe
        # are predictable from the name alone.
        name = f"{_NAME} {chr(ord('A') + index)}"
        cur.execute(
            """
            INSERT INTO mf.funds
                (amfi_scheme_code, scheme_name, scheme_name_norm, amc_id, scheme_type,
                 scheme_category, plan_type, option_type, periodicity, plan_source,
                 isin_growth_or_div_payout)
            VALUES (%s, %s, %s, %s, 'OPEN_ENDED', %s, %s, %s, %s, %s, %s)
            """,
            (code, name, name.upper(), _TEST_AMC_ID, _CAT, plan, option,
             period, plan_source, isin),
        )
        cur.execute(
            "INSERT INTO mf.fund_variants (amfi_scheme_code, group_key, base_scheme_name)"
            " VALUES (%s, %s, %s)",
            (code, _GROUP, _NAME),
        )


def _insert_nav(cur, *, ref: date) -> None:
    """Give two in-scope variants a mover-visible window: +10% and +20%.

    Dates are anchored to the database's own latest NAV date so the synthetic
    rows land inside the default 1-month window without moving the reference.
    """
    for code, first, last in ((900200, 100.0, 110.0), (900204, 100.0, 120.0)):
        cur.execute(
            "INSERT INTO mf.nav_history (amfi_scheme_code, nav_date, nav)"
            " VALUES (%s, %s, %s)",
            (code, ref - timedelta(days=8), first),
        )
        cur.execute(
            "INSERT INTO mf.nav_history (amfi_scheme_code, nav_date, nav)"
            " VALUES (%s, %s, %s)",
            (code, ref, last),
        )


def _plans(rows) -> set[str]:
    return {r["plan_type"] for r in rows}


def _ensure_schema(pg_dsn: str, repo_root: str) -> None:
    """Migrate a never-bootstrapped database; leave any other database alone.

    No suite autouse fixture creates the schema: each DSN file brings it up
    itself, and collection order decides who arrives first. This file sorts
    *before* ``test_postgres_integration.py``, so CI's empty service database
    still has no ``mf`` schema when these tests start.

    The probe is deliberately ``mf.funds``: a database that already has it is
    either populated (a local dev snapshot) or already migrated (CI), and
    ``apply_migrations`` must not be replayed into either — it is checksum
    verified and fails closed on a restored schema without a ledger.
    """
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(pg_dsn, autocommit=True)
    try:
        missing = conn.execute(
            "SELECT to_regclass('mf.funds') IS NULL AS missing"
        ).fetchone()[0]
    finally:
        conn.close()
    if missing:
        from mfdataindia.store.postgres import PostgresStore

        with PostgresStore(pg_dsn) as store:
            store.apply_migrations(Path(repo_root) / "sql")


@pytest.fixture
def scope_db(pg_dsn, repo_root):
    """Rolled-back transaction holding the synthetic plan-scope group."""
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    _ensure_schema(pg_dsn, repo_root)
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
        cur.execute("SELECT amc_id FROM mf.amcs WHERE amc_id = %s", (_TEST_AMC_ID,))
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


@pytest.fixture
def scope_db_with_nav(scope_db):
    """The same transaction plus NAV rows for the movers tests.

    ``movers`` anchors its window on the database's own ``max(nav_date)``, so a
    freshly migrated CI database — which has no NAV history at all — supplies
    today instead and the synthetic rows become the reference themselves. The
    window stays relative either way, so the same assertions hold on an empty
    and on a populated database.
    """
    cur = scope_db.cursor()
    cur.execute("SELECT max(nav_date) AS mx FROM mf.nav_history")
    ref = cur.fetchone()["mx"] or date.today()
    _insert_nav(cur, ref=ref)
    return scope_db


# -- list surfaces ------------------------------------------------------------

def test_default_scope_never_lists_direct(scope_db):
    # in_scope=False on purpose: the plan predicate must do this on its own.
    res = queries.list_funds(scope_db, q=_NAME, in_scope=False, per_page=50)
    assert _plans(res["results"]) == {"REGULAR", "UNLABELLED"}
    codes = {r["amfi_scheme_code"] for r in res["results"]}
    assert 900202 not in codes and 900203 not in codes and 900206 not in codes


def test_all_scope_admits_direct_and_retail(scope_db):
    res = queries.list_funds(scope_db, q=_NAME, in_scope=False, per_page=50,
                             plan="all")
    assert "DIRECT" in _plans(res["results"])
    assert "RETAIL" in _plans(res["results"])


def test_direct_scope_is_explicitly_narrow(scope_db):
    res = queries.list_funds(scope_db, q=_NAME, in_scope=False, per_page=50,
                             plan="direct")
    assert _plans(res["results"]) == {"DIRECT"}


def test_blank_plan_feed_row_is_not_served(scope_db):
    # UNLABELLED with plan_source='COLUMN_BLANK' is out of scope by curation;
    # the plan scope must not silently pull it back in.
    codes = {r["amfi_scheme_code"]
             for r in queries.list_funds(scope_db, q=_NAME, per_page=50)["results"]}
    assert 900204 in codes       # name-inferred Regular: served
    assert 900205 not in codes   # plan unknown: not served



def test_family_view_is_regular_represented(scope_db):
    res = queries.list_fund_families(scope_db, q=_NAME, per_page=50)
    # One family (shared group_key, AMC, type and category), represented by the
    # Regular Growth variant and counted from the four in-scope variants
    # (900200, 900201, 900204, 900207) — never the Direct or Retail ones.
    assert res["total"] == 1
    row = res["results"][0]
    assert row["amfi_scheme_code"] == 900200
    assert row["plan_type"] == "REGULAR"
    assert row["variant_count"] == 4


def test_family_widening_plan_does_not_bypass_curation(scope_db):
    # plan='all' must not smuggle Direct rows past in_scope.
    default = queries.list_fund_families(scope_db, q=_NAME, per_page=50)
    widened = queries.list_fund_families(scope_db, q=_NAME, per_page=50, plan="all")
    assert widened["total"] == default["total"]
    assert widened["results"][0]["variant_count"] == default["results"][0]["variant_count"]


# -- sibling navigation -------------------------------------------------------

def test_siblings_exclude_direct_by_default(scope_db):
    d = queries.fund_detail(scope_db, 900200)
    assert d is not None
    assert _plans(d["siblings"]) == {"REGULAR", "UNLABELLED"}
    codes = {s["amfi_scheme_code"] for s in d["siblings"]}
    assert 900202 not in codes and 900203 not in codes


def test_siblings_include_direct_only_when_asked(scope_db):
    d = queries.fund_detail(scope_db, 900200, plan="all")
    assert 900202 in {s["amfi_scheme_code"] for s in d["siblings"]}


def test_direct_page_offers_regular_twins(scope_db):
    # A Direct code stays resolvable by code, but every hop it offers leads back
    # into the served universe.
    d = queries.fund_detail(scope_db, 900202)
    assert d["plan_type"] == "DIRECT"
    assert _plans(d["siblings"]) == {"REGULAR", "UNLABELLED"}


# -- suggest ------------------------------------------------------------------

def test_suggest_never_returns_direct(scope_db):
    rows = queries.suggest(scope_db, _NAME, limit=25)
    assert rows
    assert _plans(rows) == {"REGULAR", "UNLABELLED"}


def test_suggest_cannot_resolve_a_direct_code(scope_db):
    assert queries.suggest(scope_db, "900202") == []


def test_suggest_does_not_rank_isin_less_schemes_first(scope_db):
    # 900200 carries an ISIN and sorts first by name; 900207 has none and sorts
    # last. ``isin_primary = q`` is NULL for 900207, and FALSE OR NULL is NULL,
    # which Postgres puts FIRST under DESC — so before the coalesce fix the
    # ISIN-less scheme won the top slot.
    rows = queries.suggest(scope_db, _NAME, limit=25)
    codes = [r["amfi_scheme_code"] for r in rows]
    assert codes[0] == 900200
    assert 900207 in codes


def test_suggest_order_is_deterministic(scope_db):
    first = [r["amfi_scheme_code"] for r in queries.suggest(scope_db, _NAME, limit=25)]
    second = [r["amfi_scheme_code"] for r in queries.suggest(scope_db, _NAME, limit=25)]
    assert first == second
    assert first == sorted(first)  # identical names -> ascending code



# -- movers -------------------------------------------------------------------

def test_movers_include_served_variants(scope_db_with_nav):
    codes = {r["amfi_scheme_code"]
             for r in queries.movers(scope_db_with_nav, limit=50)["results"]}
    assert {900200, 900204} <= codes


def test_movers_honour_the_plan_scope_where_in_scope_alone_would_not(scope_db_with_nav):
    # 900204 is UNLABELLED-but-name-inferred, so in_scope admits it. Only the
    # plan predicate can take it out — which is how we know it is wired in.
    narrow = {r["amfi_scheme_code"]
              for r in queries.movers(scope_db_with_nav, limit=50, plan="direct")["results"]}
    assert 900204 not in narrow and 900200 not in narrow


def test_category_movers_honour_the_plan_scope(scope_db_with_nav):
    # category_movers collapses variants onto one row per scheme family, so the
    # +20% 900204 represents the synthetic family. 900204 is UNLABELLED-but-name
    # -inferred: only the plan predicate can remove it, which is the wiring proof.
    default = queries.category_movers(scope_db_with_nav, limit=50)
    narrow = queries.category_movers(scope_db_with_nav, limit=50, plan="direct")
    flat = {item["amfi_scheme_code"] for cat in default["categories"]
            for item in [*cat["gainers"], *cat["losers"]]}
    narrow_flat = {item["amfi_scheme_code"] for cat in narrow["categories"]
                   for item in [*cat["gainers"], *cat["losers"]]}
    assert 900204 in flat
    assert 900204 not in narrow_flat


# -- facet vocabulary ---------------------------------------------------------

def test_facet_counts_use_the_served_scope(scope_db):
    row = next(r for r in queries.amcs(scope_db)
               if r["amfi_amc_name"] == "Plan Scope Test AMC")
    # Four of the eight synthetic variants are served (Regular x3 + the
    # name-inferred one); the facet must count exactly those, so a partner never
    # sees a promise the list cannot keep.
    assert row["live_funds"] == 4


# -- populated-database zero-change guard ------------------------------------

def test_served_universe_is_unchanged_by_the_default(pg_dsn, repo_root):
    # in_scope already excludes Direct, so the new predicate must not change a
    # single row of what the Insights consumer sees today. If this ever fails,
    # ingest has started marking Direct or Retail rows as in scope.
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    _ensure_schema(pg_dsn, repo_root)
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    conn = psycopg.connect(pg_dsn, row_factory=dict_row, autocommit=True)
    try:
        served = queries.list_funds(conn, per_page=1)["total"]
        if served == 0:
            # A freshly migrated CI database has nothing to compare, and this
            # would pass for the wrong reason. Report the skip rather than a win.
            pytest.skip("database serves no funds; this guard needs a populated one")
        assert served == queries.list_funds(conn, per_page=1, plan="all")["total"]
        assert (queries.list_fund_families(conn, per_page=1)["total"]
                == queries.list_fund_families(conn, per_page=1, plan="all")["total"])
        assert all(row["plan_type"] in {"REGULAR", "UNLABELLED"}
                   for row in queries.list_funds(conn, per_page=500)["results"])
    finally:
        conn.close()


# -- rollback ----------------------------------------------------------------

def test_rollback_leaves_no_rows(pg_dsn, repo_root):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    _ensure_schema(pg_dsn, repo_root)
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(pg_dsn, autocommit=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM mf.funds WHERE amfi_scheme_code = ANY(%s::int[])",
            (list(_TEST_CODES),),
        )
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM mf.amcs WHERE amc_id = %s", (_TEST_AMC_ID,))
        assert cur.fetchone()[0] == 0
    finally:
        conn.close()

