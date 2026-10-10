"""last_seen_in_source regression tests: correct source-presence tracking.

Every scheme PRESENT in a successful AMFI refresh must advance
``mf.funds.last_seen_in_source`` to that refresh's source date, even when all
other metadata is unchanged — presence in the feed is lifecycle evidence.
Schemes ABSENT from the feed keep their previous last-seen date, and neither
absence nor a stale NAV alone may produce an authoritative redeemed/matured
classification. Replaying an older snapshot cannot regress the date. The AMFI
REDEEMED marker keeps producing the existing
``LIFECYCLE_ENDED`` evidence.

Safe to run against a populated database: the synthetic schemes (9003xx AMFI
codes, AMC 900003) are inserted inside a single transaction that is always
rolled back. If any synthetic identifier already exists the fixture fails
loudly instead of touching real rows. Skipped unless ``MF_TEST_DSN`` is set.

The tests drive the real ingest path end to end: :func:`fund_rows` /
:func:`quality_flag_rows` map the parsed schemes, and the store's own staging
DDL + set-based merge (``_merge_funds``) apply them — the exact SQL that
``load_funds`` runs — on the rolled-back connection.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from mfdataindia.load.amfi_to_store import fund_rows, quality_flag_rows
from mfdataindia.store.postgres import FUND_COLUMNS, PostgresStore

pytestmark = pytest.mark.postgres

_TEST_AMC_ID = 900003
_TEST_AMC_NAME = "Last Seen Test AMC"
_CODES = (900300, 900301, 900302)  # unchanged / absent / redeemed-marker
_NAMES = {
    900300: "Last Seen Unchanged Fund",
    900301: "Last Seen Absent Fund",
    900302: "Last Seen Redeemed Fund",
}
_CAT = "Debt Scheme - Liquid Fund"
_TYPE = "Open Ended Schemes"
#: Refresh #1 (inserts the fixtures) and refresh #2 (the assertions live here).
_D1 = date(2026, 6, 1)
_D2 = date(2026, 6, 2)
#: 900301's last NAV is far behind both refreshes: stale on purpose.
_STALE_NAV_DATE = _D1 - timedelta(days=300)


def _make_scheme_factory():
    from conftest import make_scheme

    return make_scheme


def _scheme(factory, code: int, *, redeemed: bool, nav_date: date):
    isin = "INFLST" + f"{code:06d}"
    return factory(
        amfi_scheme_code=str(code),
        scheme_name=_NAMES[code],
        nav=100.0,
        nav_date=nav_date,
        scheme_type=_TYPE,
        scheme_category=_CAT,
        amc=_TEST_AMC_NAME,
        isin_div_payout_or_growth=isin,
        isin_div_reinvestment="REDEEMED" if redeemed else None,
        plan_type="REGULAR",
        option="GROWTH",
        periodicity=None,
        is_etf=False,
        is_defunct=False,
        nav_not_published=False,
        in_scope=True,
        plan_source="COLUMN",
        option_source="COLUMN",
    )


def _ensure_schema(pg_dsn: str, repo_root: str) -> None:
    """Migrate a never-bootstrapped database; leave any other database alone.

    Mirrors ``test_plan_scope._ensure_schema``: this file sorts before
    ``test_postgres_integration.py``, so CI's empty service database still has
    no ``mf`` schema when these tests start, and a populated dev snapshot must
    never be re-migrated (the runner is checksum-verified and fails closed).
    """
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(pg_dsn, autocommit=True)
    try:
        missing = conn.execute("SELECT to_regclass('mf.funds') IS NULL AS missing").fetchone()[0]
    finally:
        conn.close()
    if missing:
        with PostgresStore(pg_dsn) as store:
            store.apply_migrations(Path(repo_root) / "sql")


@pytest.fixture
def last_seen_db(pg_dsn, repo_root):
    """Rolled-back transaction holding the synthetic 9003xx scheme group.

    Yields the live psycopg connection; the merge is driven through the
    store's private helpers so the exact production SQL runs, but nothing is
    ever committed.
    """
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    _ensure_schema(pg_dsn, repo_root)
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    conn = psycopg.connect(pg_dsn, row_factory=dict_row, autocommit=False)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT amfi_scheme_code FROM mf.funds WHERE amfi_scheme_code = ANY(%s::int[])",
            (list(_CODES),),
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
        store = PostgresStore(pg_dsn, use_copy=False)
        yield {"conn": conn, "cur": cur, "store": store}
    finally:
        conn.rollback()
        conn.close()


def _fund_fixture_rows(source_date: date):
    """One fund_rows tuple per code, from the refresh-#1 feed."""
    factory = _make_scheme_factory()
    schemes = [_scheme(factory, c, redeemed=False, nav_date=source_date) for c in _CODES]
    return list(fund_rows(schemes, source_date=source_date))


def _stage_and_merge(db, rows) -> tuple[int, int, int]:
    """Run the exact staging + merge that load_funds() executes."""
    store: PostgresStore = db["store"]
    cur = db["cur"]
    store._create_staging(cur, "stg_funds", store._STG_FUNDS_DDL)
    staged = store._copy_rows(cur, "stg_funds", FUND_COLUMNS, iter(rows))
    if staged:
        updated, inserted, unchanged = store._merge_funds(cur, staged)
    else:
        updated = inserted = unchanged = 0
    store._drop(cur, "stg_funds")
    return updated, inserted, unchanged


def _refresh2(db) -> None:
    """Feed refresh #2: 900300 unchanged, 900301 absent, 900302 REDEEMED."""
    factory = _make_scheme_factory()
    schemes = [
        _scheme(factory, 900300, redeemed=False, nav_date=_D2),
        _scheme(factory, 900302, redeemed=True, nav_date=_D2),
    ]
    updated, inserted, unchanged = _stage_and_merge(db, list(fund_rows(schemes, source_date=_D2)))
    db["refresh2"] = {"updated": updated, "inserted": inserted, "unchanged": unchanged}
    # Persist the refresh's quality flags (the LIFECYCLE_ENDED evidence path).
    flag_rows = list(quality_flag_rows(schemes))
    db["flag_rows"] = flag_rows
    if flag_rows:
        cur = db["cur"]
        cur.executemany(
            """
            INSERT INTO mf.quality_flags (
                amfi_scheme_code, nav_date, flag_type, severity, message, details, source
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            [(r[0], r[1], r[2], r[3], r[4], r[5], "AMFI_LASTSEEN_TEST") for r in flag_rows],
        )


def _setup_refresh1(db) -> None:
    cur = db["cur"]
    cur.execute(
        "INSERT INTO mf.amcs (amc_id, amfi_amc_name, normalised_name) "
        "OVERRIDING SYSTEM VALUE VALUES (%s, %s, %s)",
        (_TEST_AMC_ID, _TEST_AMC_NAME, "last seen test amc"),
    )
    _stage_and_merge(db, _fund_fixture_rows(_D1))
    # Stale NAV for the absent scheme + a normal NAV for the unchanged one.
    cur.execute(
        "INSERT INTO mf.nav_history (amfi_scheme_code, nav_date, nav) VALUES (%s, %s, %s), (%s, %s, %s)",
        (900301, _STALE_NAV_DATE, 50.0, 900300, _D1, 100.0),
    )


def _fund(db, code: int) -> dict:
    row = (
        db["cur"]
        .execute(
            "SELECT f.*, a.amfi_amc_name FROM mf.funds f "
            "JOIN mf.amcs a ON a.amc_id = f.amc_id WHERE f.amfi_scheme_code = %s",
            (code,),
        )
        .fetchone()
    )
    assert row is not None, f"fixture fund {code} missing"
    return dict(row)


def _lifecycle_flags_for(db, *codes: int) -> list[dict]:
    rows = (
        db["cur"]
        .execute(
            "SELECT amfi_scheme_code, flag_type FROM mf.quality_flags "
            "WHERE amfi_scheme_code = ANY(%s::int[]) "
            "  AND flag_type IN ('LIFECYCLE_ENDED', 'DEAD_SCHEME')",
            (list(codes),),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def test_unchanged_scheme_advances_last_seen(last_seen_db):
    """A scheme present in refresh #2 with byte-identical metadata still
    advances last_seen_in_source to that refresh's source date, and
    first_seen_in_source is never overwritten."""
    db = last_seen_db
    _setup_refresh1(db)
    before = _fund(db, 900300)
    assert before["last_seen_in_source"] == _D1
    assert before["first_seen_in_source"] == _D1

    _refresh2(db)
    after = _fund(db, 900300)
    assert after["last_seen_in_source"] == _D2
    assert after["first_seen_in_source"] == _D1
    # Nothing else moved: the presence guard must not rewrite metadata.
    for col in (
        "scheme_name",
        "scheme_name_norm",
        "scheme_type",
        "scheme_category",
        "plan_type",
        "plan_source",
        "option_type",
        "periodicity",
        "is_etf",
        "is_defunct",
        "nav_not_published",
        "isin_growth_or_div_payout",
        "isin_div_reinvest",
        "amc_id",
    ):
        assert after[col] == before[col], f"{col} changed for an unchanged scheme"
    assert after["is_active"] is True


def test_absent_scheme_retains_previous_last_seen(last_seen_db):
    """A scheme missing from the new feed keeps its last-seen evidence: it is
    not deleted, not zeroed, and not re-dated."""
    db = last_seen_db
    _setup_refresh1(db)
    _refresh2(db)
    after = _fund(db, 900301)
    assert after["last_seen_in_source"] == _D1
    assert after["first_seen_in_source"] == _D1
    assert after["is_active"] is True
    assert after["is_defunct"] is False


def test_amfi_redeemed_marker_produces_lifecycle_ended_evidence(last_seen_db):
    """The REDEEMED marker keeps producing the existing LIFECYCLE_ENDED
    evidence (quality flag + suppressed is_active), now alongside the
    advanced last-seen date."""
    db = last_seen_db
    _setup_refresh1(db)
    _refresh2(db)
    after = _fund(db, 900302)
    assert after["last_seen_in_source"] == _D2
    assert after["is_active"] is False
    assert after["is_defunct"] is False

    # The mapping emitted the existing evidence type for the marker code only.
    flagged = [r for r in db["flag_rows"] if r[0] is not None]
    types_by_code = {r[0]: r[2] for r in flagged}
    assert types_by_code.get(900302) == "LIFECYCLE_ENDED"
    assert 900300 not in types_by_code
    assert 900301 not in types_by_code

    # And it is durable: the refresh's evidence row is queryable.
    import json as _json

    rows = _lifecycle_flags_for(db, 900302)
    assert len(rows) == 1 and rows[0]["flag_type"] == "LIFECYCLE_ENDED"
    detail = (
        db["cur"]
        .execute(
            "SELECT details FROM mf.quality_flags WHERE amfi_scheme_code = %s "
            "AND flag_type = 'LIFECYCLE_ENDED'",
            (900302,),
        )
        .fetchone()["details"]
    )
    detail = detail if isinstance(detail, dict) else _json.loads(detail)
    assert detail["raw_value"] == "REDEEMED"


def test_absence_or_stale_nav_never_produces_redeemed_or_matured(last_seen_db):
    """The absent scheme's NAV is 300 days stale and the unchanged scheme's
    NAV lags the refresh — neither may gain an authoritative redeemed or
    matured (LIFECYCLE_ENDED/DEAD_SCHEME) classification from this refresh."""
    db = last_seen_db
    _setup_refresh1(db)
    _refresh2(db)

    assert _lifecycle_flags_for(db, 900300, 900301) == []
    # The refresh's own flag output carries no lifecycle row for the absent
    # code (or the stale-NAV code).
    for r in db["flag_rows"]:
        assert r[0] not in (900300, 900301)
    assert _fund(db, 900301)["is_active"] is True
    assert _fund(db, 900301)["is_defunct"] is False
    assert _fund(db, 900300)["is_active"] is True


def test_last_seen_guard_changes_unchanged_counting(last_seen_db):
    """The merge must count the presence-driven refresh as an update, not an
    unchanged row — otherwise a daily re-ingest would silently stop recording
    source presence (the original bug)."""
    db = last_seen_db
    _setup_refresh1(db)
    inserted1 = (
        db["cur"]
        .execute(
            "SELECT count(*) AS n FROM mf.funds WHERE amfi_scheme_code = ANY(%s::int[])",
            (list(_CODES),),
        )
        .fetchone()["n"]
    )
    assert inserted1 == 3

    _refresh2(db)
    assert db["refresh2"]["updated"] == 2  # 900300 (presence only) + 900302
    assert db["refresh2"]["inserted"] == 0
    assert db["refresh2"]["unchanged"] == 0


def test_older_snapshot_replay_cannot_regress_last_seen(last_seen_db):
    """Historical replays may rebuild NAV history, but source-presence
    evidence is monotonic and must never move backwards."""
    db = last_seen_db
    _setup_refresh1(db)
    _refresh2(db)
    assert _fund(db, 900300)["last_seen_in_source"] == _D2

    factory = _make_scheme_factory()
    replay = _scheme(factory, 900300, redeemed=False, nav_date=_D1)
    updated, inserted, unchanged = _stage_and_merge(
        db, list(fund_rows([replay], source_date=_D1))
    )

    assert (updated, inserted, unchanged) == (0, 0, 1)
    assert _fund(db, 900300)["last_seen_in_source"] == _D2
