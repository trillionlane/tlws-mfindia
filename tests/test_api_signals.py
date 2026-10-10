"""API signal tests: lifecycle, freshness, quality, methodology, eligibility.

Fixture-based on a throwaway scratch database: the fixtures must be COMMITTED
for the API's connection pool to see them (a rolled-back transaction would be
invisible to it), so this file creates its own database (migrated + seeded),
runs the real app against it, and drops the database again. Nothing durable
is ever touched in the target database. Skipped unless ``MF_TEST_DSN`` is set
and the database user may create databases.

Covers the sprint's required cases:
* current active Growth scheme (comparison-eligible);
* IDCW scheme with comparison_eligible=false;
* AMFI REDEEMED scheme (authoritative marker -> redeemed);
* close-ended stale scheme without authoritative maturity evidence -> unknown;
* no-NAV scheme (freshness missing, nav_series_unavailable);
* constant-series candidate and exact duplicate-variant candidate (quality);
* stale assessment version (assessment_status=stale, never "clean");
* siblings with distinct periodicity;
* legacy ACT/ALL facts that do not become a buyability signal;
* movers exclude comparison-ineligible series;
* detail requests read the assessment table (not a history scan): a scheme is
  ``not_assessed`` until the audit has written a row.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres

_D0 = date(2026, 1, 1)
_SNAPSHOT = _D0 + timedelta(days=40)      # latest source snapshot date
_NAV_REF = _SNAPSHOT                      # newest NAV in the scratch DB
_AMC = 900006
#: code -> role in the fixture set
GROWTH_ACTIVE = 900500
IDCW = 900501
REDEEMED = 900502
CLOSE_ENDED_STALE = 900503
NO_NAV = 900504
CONSTANT = 900505
DUP_A = 900506
DUP_B = 900507
MOVER_GROWTH = 900508
MOVER_IDCW = 900509
_CATS = "Debt Scheme - Liquid Fund"


def _dsn_with_db(pg_dsn: str, dbname: str) -> str:
    """Rewrite the database name in a DSN (URL or keyword form)."""
    if pg_dsn.startswith("postgresql://") or pg_dsn.startswith("postgres://"):
        head, _, tail = pg_dsn.partition(f"/{pg_dsn.split('/')[-1].split('?')[0]}")
        rest = "?" + pg_dsn.split("?", 1)[1] if "?" in pg_dsn else ""
        return f"{head}/{dbname}{rest}"
    parts = {}
    for tok in pg_dsn.replace(" ", "").split("host=")[0].split(","):
        pass
    # keyword form: host=... port=... dbname=... user=... password=***
    import re
    out = []
    for m in re.finditer(r"(\w+)=('[^']*'|\S+)", pg_dsn):
        k, v = m.group(1), m.group(2)
        if k == "dbname":
            v = dbname
        out.append(f"{k}={v}")
    return " ".join(out)


def _ensure_createdb(pg_dsn: str) -> bool:
    psycopg = pytest.importorskip("psycopg")
    try:
        conn = psycopg.connect(pg_dsn, autocommit=True)
        row = conn.execute(
            "SELECT coalesce(rolsuper, false) OR coalesce(rolcreatedb, false) "
            "FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        conn.close()
        return bool(row[0])
    except Exception:
        return False


@contextmanager
def _scratch_database(pg_dsn: str, repo_root: str):
    """Yield (dsn, psycopg) for a freshly migrated, disposable database."""
    psycopg = pytest.importorskip("psycopg")
    dbname = f"mfdata_api_signals_{os.getpid()}"
    admin = psycopg.connect(pg_dsn, autocommit=True)
    admin.execute(f'CREATE DATABASE "{dbname}"')
    admin.close()
    dsn = _dsn_with_db(pg_dsn, dbname)
    try:
        from mfdataindia.store.postgres import PostgresStore

        with PostgresStore(dsn) as store:
            store.apply_migrations(Path(repo_root) / "sql")
        yield dsn
    finally:
        admin = psycopg.connect(pg_dsn, autocommit=True)
        admin.execute(
            f'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
            f"WHERE datname = '{dbname}' AND pid <> pg_backend_pid()"
        )
        admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        admin.close()


def _seed(dsn: str) -> None:
    """Commit the synthetic fixture set into the scratch database."""
    from mfdataindia.audit import nav_integrity
    from psycopg.rows import dict_row

    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO mf.amcs (amc_id, amfi_amc_name, normalised_name) "
        "OVERRIDING SYSTEM VALUE VALUES (%s, 'Signal Test AMC', 'signal test amc')",
        (_AMC,),
    )

    def fund(code: int, *, option: str = "GROWTH", plan: str = "REGULAR",
             scheme_type: str = "OPEN_ENDED", category: str = _CATS,
             periodicity: str | None = None, is_defunct: bool = False,
             is_active: bool = True, last_seen: date | None = _SNAPSHOT,
             group: str | None = None, name: str | None = None):
        cur.execute(
            """
            INSERT INTO mf.funds (amfi_scheme_code, scheme_name, scheme_name_norm,
                                  amc_id, scheme_type, scheme_category, plan_type,
                                  option_type, periodicity, is_defunct, is_active,
                                  last_seen_in_source, first_seen_in_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (code, name or f"Signal Fund {code}", (name or f"signal fund {code}").upper(),
             _AMC, scheme_type, category, plan, option, periodicity,
             is_defunct, is_active, last_seen, _D0),
        )
        if group:
            cur.execute(
                "INSERT INTO mf.fund_variants (amfi_scheme_code, group_key, base_scheme_name) "
                "VALUES (%s, %s, %s)",
                (code, group, name or f"Signal Fund {code}"),
            )

    def nav(code: int, pairs):
        cur.executemany(
            "INSERT INTO mf.nav_history (amfi_scheme_code, nav_date, nav) "
            "VALUES (%s, %s, %s)",
            [(code, d, v) for d, v in pairs],
        )

    # 1) current active growth: present in the latest snapshot, fresh NAV
    fund(GROWTH_ACTIVE, group="SIG-G")
    fund(GROWTH_ACTIVE + 100, option="IDCW", periodicity="DAILY", group="SIG-G")
    fund(GROWTH_ACTIVE + 101, option="IDCW", periodicity="WEEKLY", group="SIG-G")
    nav(GROWTH_ACTIVE, [(_NAV_REF - timedelta(days=i), 100.0 + i) for i in range(40)])
    # 2) IDCW: fresh, but comparison-ineligible by construction
    fund(IDCW, option="IDCW", periodicity="MONTHLY")
    nav(IDCW, [(_NAV_REF - timedelta(days=i), 50.0 + i * 0.1) for i in range(40)])
    # 3) AMFI REDEEMED marker: authoritative LIFECYCLE_ENDED evidence
    fund(REDEEMED)
    nav(REDEEMED, [(_NAV_REF - timedelta(days=1), 10.0)])
    cur.execute(
        """
        INSERT INTO mf.quality_flags (amfi_scheme_code, nav_date, flag_type,
                                      severity, message, details, source)
        VALUES (%s, %s, 'LIFECYCLE_ENDED', 'INFO',
                'AMFI marked the ISIN column REDEEMED',
                '{"raw_value": "REDEEMED"}', 'AMFI')
        """,
        (REDEEMED, _NAV_REF - timedelta(days=1)),
    )
    # 4) close-ended stale scheme WITHOUT any authoritative maturity evidence
    fund(CLOSE_ENDED_STALE, scheme_type="CLOSE_ENDED",
         last_seen=_SNAPSHOT - timedelta(days=120))
    nav(CLOSE_ENDED_STALE, [(_SNAPSHOT - timedelta(days=120 + i), 12.0 + i)
                            for i in range(10)])
    # 5) no-NAV scheme
    fund(NO_NAV)
    # 6) constant series candidate (260 identical observations)
    fund(CONSTANT)
    nav(CONSTANT, [(_NAV_REF - timedelta(days=259 - i), 10.0) for i in range(260)])
    # 7) exact duplicate variants in one guarded family
    fund(DUP_A, group="SIG-DUP")
    fund(DUP_B, option="IDCW", group="SIG-DUP")
    series = [(_D0 + timedelta(days=i), 5.0 + i * 0.5) for i in range(30)]
    nav(DUP_A, series)
    nav(DUP_B, series)
    # 8) movers: a strong growth mover and a stronger IDCW mover (must be excluded)
    fund(MOVER_GROWTH)
    fund(MOVER_IDCW, option="IDCW")
    nav(MOVER_GROWTH, [(_NAV_REF - timedelta(days=10), 100.0), (_NAV_REF, 110.0)])
    nav(MOVER_IDCW, [(_NAV_REF - timedelta(days=10), 100.0), (_NAV_REF, 130.0)])

    # legacy facts: ACT/ALL must stay a passive fact, never a buyability signal
    cur.execute(
        """
        INSERT INTO mf.fund_facts (amfi_scheme_code, aum, status, transaction_status,
                                   source)
        VALUES (%s, 1000.0, 'ACT', 'ALL', 'LEGACY')
        """,
        (GROWTH_ACTIVE,),
    )

    # Bump the dataset version first (fresh DB starts at 1) so the stale
    # case below can age a row without tripping the > 0 CHECK.
    for _ in range(2):
        conn.execute(
            "SELECT mf.refresh_dataset_summary('signal_test_seed', NULL)"
        )
    dv = conn.execute(
        "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
    ).fetchone()["dataset_version"]
    assert dv >= 2

    # run the real audit so the assessment table is populated (committed) at
    # the CURRENT dataset version — everything reads "current" afterwards.
    with conn.transaction():
        nav_integrity.assess(conn, write=True)

    # stale-assessment case: age exactly one row below the current version.
    conn.execute(
        "UPDATE mf.nav_quality_assessments SET assessed_dataset_version = %s "
        "WHERE amfi_scheme_code = %s",
        (dv - 1, CONSTANT),
    )
    conn.close()


@pytest.fixture(scope="module")
def api_signals(pg_dsn, repo_root):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    if not _ensure_createdb(pg_dsn):
        pytest.skip("database user lacks CREATE privilege; skipping scratch-DB suite")
    from fastapi.testclient import TestClient
    from mfdataindia.api.app import create_app

    with _scratch_database(pg_dsn, repo_root) as dsn:
        _seed(dsn)
        app = create_app(dsn)
        with TestClient(app) as client:
            yield client


def test_active_growth_scheme_is_comparison_eligible(api_signals):
    d = api_signals.get(f"/api/funds/{GROWTH_ACTIVE}").json()
    assert d["lifecycle"] == {
        "state": "active", "evidence": "amfi_current_feed",
        "last_seen_in_source": _SNAPSHOT.isoformat(),
    }
    assert d["nav_freshness"]["status"] == "current"
    assert d["nav_freshness"]["lag_days"] == 0
    r = api_signals.get(f"/api/funds/{GROWTH_ACTIVE}/returns").json()
    m = r["methodology"]
    assert m["basis"] == "nav_change"
    assert m["distribution_adjustment"] == "not_applicable"
    assert m["comparison_eligible"] is True
    assert m["limitations"] == []
    a = api_signals.get(f"/api/funds/{GROWTH_ACTIVE}/analytics").json()
    assert a["methodology"]["comparison_eligible"] is True


def test_idcw_scheme_is_comparison_ineligible(api_signals):
    r = api_signals.get(f"/api/funds/{IDCW}/returns").json()
    m = r["methodology"]
    assert m["basis"] == "nav_change"
    assert m["distribution_adjustment"] == "unavailable"
    assert m["comparison_eligible"] is False
    assert "distribution_history_unavailable" in m["limitations"]
    a = api_signals.get(f"/api/funds/{IDCW}/analytics").json()
    assert a["methodology"]["comparison_eligible"] is False
    # the raw NAV-based numbers are still served on the direct request
    assert r["horizons"]["1M"] is not None


def test_amfi_redeemed_marker_produces_redeemed_state(api_signals):
    d = api_signals.get(f"/api/funds/{REDEEMED}").json()
    assert d["lifecycle"]["state"] == "redeemed"
    assert d["lifecycle"]["evidence"] == "amfi_redeemed_marker"


def test_close_ended_stale_without_evidence_is_never_matured(api_signals):
    d = api_signals.get(f"/api/funds/{CLOSE_ENDED_STALE}").json()
    lc = d["lifecycle"]
    assert lc["state"] == "unknown"
    assert lc["evidence"] == "not_seen_in_latest_feed"
    # a terminal-10.00 close-ended fund with no marker is NOT redeemed
    assert lc["state"] != "redeemed"
    assert lc["state"] != "defunct"
    # and freshness reflects the long lag without inventing lifecycle
    assert d["nav_freshness"]["status"] in {"stale", "very_stale"}


def test_no_nav_scheme_reports_missing(api_signals):
    d = api_signals.get(f"/api/funds/{NO_NAV}").json()
    assert d["nav_freshness"] == {
        "dataset_as_of": _NAV_REF.isoformat(),
        "latest_nav_date": None,
        "lag_days": None,
        "status": "missing",
    }
    r = api_signals.get(f"/api/funds/{NO_NAV}/returns").json()
    assert r["horizons"] == {}
    m = r["methodology"]
    assert m["comparison_eligible"] is False
    assert "nav_series_unavailable" in m["limitations"]


def test_constant_and_duplicate_signals_are_exposed(api_signals):
    d = api_signals.get(f"/api/funds/{CONSTANT}").json()
    q = d["nav_quality"]
    assert q["signals"] == ["constant_nav_series"]
    assert q["observation_count"] == 260
    assert q["distinct_nav_count"] == 1
    # the fixture aged this row one version: stale, never "clean"
    assert q["assessment_status"] == "stale"
    da = api_signals.get(f"/api/funds/{DUP_A}").json()["nav_quality"]
    db_ = api_signals.get(f"/api/funds/{DUP_B}").json()["nav_quality"]
    assert da["assessment_status"] == "current"
    assert da["signals"] == ["duplicate_variant_series"]
    assert db_["signals"] == ["duplicate_variant_series"]
    # the constant fund's methodology gate fails on the quality signal
    m = api_signals.get(f"/api/funds/{CONSTANT}/returns").json()["methodology"]
    assert m["comparison_eligible"] is False
    assert "nav_quality_signal" in m["limitations"]


def test_siblings_carry_periodicity(api_signals):
    d = api_signals.get(f"/api/funds/{GROWTH_ACTIVE}").json()
    per = {s["periodicity"] for s in d["siblings"]}
    assert per == {None, "DAILY", "WEEKLY"}


def test_legacy_act_all_never_becomes_buyability(api_signals):
    d = api_signals.get(f"/api/funds/{GROWTH_ACTIVE}").json()
    assert d["facts"]["status"] == "ACT"
    assert d["facts"]["transaction_status"] == "ALL"
    # no buyability translation anywhere in the response
    flat = _flatten_keys(d)
    assert not any("transaction_available" in k or "buyable" in k
                   or "purchasable" in k for k in flat)
    # lifecycle/quality never depend on those facts: both stay well-defined
    assert d["lifecycle"]["state"] == "active"


def test_movers_exclude_comparison_ineligible(api_signals):
    d = api_signals.get("/api/movers?period=1m&limit=50").json()
    codes = {r["amfi_scheme_code"] for r in d["results"]}
    assert MOVER_GROWTH in codes
    assert MOVER_IDCW not in codes
    opts = {r["option_type"] for r in d["results"]}
    assert not (opts & {"IDCW", "DIVIDEND"})
    d2 = api_signals.get("/api/movers/categories?period=1m&limit=5").json()
    for cat in d2["categories"]:
        for r in cat["gainers"] + cat["losers"]:
            assert r["option_type"] not in {"IDCW", "DIVIDEND"}


def test_detail_does_not_require_a_quality_scan(api_signals):
    # The audit writes a row per scheme that HAS NAV (assessed-and-clean =
    # empty signals); a scheme with no NAV at all has no row and reads
    # not_assessed. Either way the detail path only reads the assessment
    # table — it never scans history to decide.
    d = api_signals.get(f"/api/funds/{NO_NAV}").json()
    assert d["nav_quality"]["assessment_status"] == "not_assessed"
    assert d["nav_quality"]["signals"] is None
    clean = api_signals.get(f"/api/funds/{MOVER_GROWTH}").json()["nav_quality"]
    assert clean["assessment_status"] == "current"
    assert clean["signals"] == []


def _flatten_keys(obj, prefix="") -> list[str]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.append(key)
            out.extend(_flatten_keys(v, key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_flatten_keys(v, f"{prefix}[{i}]"))
    return out
