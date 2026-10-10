"""NAV-integrity audit tests: detection signals, safety, idempotency.

Fixture-based (no mutable production counts are contractual): one synthetic
AMC group (9004xx codes, AMCs 900004/900005) with hand-built NAV series
covering every signal — and every non-signal — case, assessed through the real
``mfdataindia.audit.nav_integrity`` logic. Everything runs inside a single
transaction that is always rolled back, so the tests are safe on a populated
database and make zero durable changes. Skipped unless ``MF_TEST_DSN`` is set.

Safety assertions:
* the audit produces ZERO mf.nav_history changes (row count + full content
  hash are compared around the run);
* the only write target is mf.nav_quality_assessments;
* the summary is finite JSON (json-serializable, sorted codes).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from mfdataindia.audit import nav_integrity

pytestmark = pytest.mark.postgres

_AMC_A = 900004   # Audit Test AMC A
_AMC_B = 900005   # Audit Test AMC B (guarded-identity control)
_CATS = "Debt Scheme - Liquid Fund"
_CODES = tuple(range(900400, 900409))
D0 = date(2026, 1, 1)


def _nav(cur, code: int, n: int, start: float, step: float = 0.0) -> None:
    rows = [(code, D0 + timedelta(days=i), round(start + i * step, 4)) for i in range(n)]
    cur.executemany(
        "INSERT INTO mf.nav_history (amfi_scheme_code, nav_date, nav) VALUES (%s, %s, %s)",
        rows,
    )


def _fund(cur, code: int, *, amc_id: int, category: str, group_key: str) -> None:
    cur.execute(
        """
        INSERT INTO mf.funds (amfi_scheme_code, scheme_name, scheme_name_norm, amc_id,
                              scheme_type, scheme_category, plan_type, option_type)
        VALUES (%s, %s, %s, %s, 'OPEN_ENDED', %s, 'REGULAR', 'GROWTH')
        """,
        (code, f"Audit Test Fund {code}", f"AUDIT TEST FUND {code}", amc_id, category),
    )
    cur.execute(
        "INSERT INTO mf.fund_variants (amfi_scheme_code, group_key, base_scheme_name) "
        "VALUES (%s, %s, %s)",
        (code, group_key, f"Audit Test Fund {code}"),
    )


def _ensure_schema(pg_dsn: str, repo_root: str) -> None:
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


def _nav_history_state(cur) -> tuple[int, str]:
    row = cur.execute(
        """
        SELECT count(*) AS n,
               coalesce(md5(string_agg(n.*::text, ',' ORDER BY
                                       n.amfi_scheme_code, n.nav_date)), '') AS h
        FROM mf.nav_history n
        """
    ).fetchone()
    return int(row["n"]), row["h"]


@pytest.fixture(scope="module")
def audit_db(pg_dsn, repo_root):
    """Rolled-back transaction with the synthetic audit series group."""
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
        cur.execute(
            "SELECT amc_id FROM mf.amcs WHERE amc_id = ANY(%s::int[])",
            ([_AMC_A, _AMC_B],),
        )
        amc_preexisting = [r["amc_id"] for r in cur.fetchall()]
        if preexisting or amc_preexisting:
            conn.rollback()
            pytest.fail(
                "synthetic identifiers already exist "
                f"(funds: {preexisting}, amcs: {amc_preexisting})"
            )
        for amc_id, name in ((_AMC_A, "Audit Test AMC A"), (_AMC_B, "Audit Test AMC B")):
            cur.execute(
                "INSERT INTO mf.amcs (amc_id, amfi_amc_name, normalised_name) "
                "OVERRIDING SYSTEM VALUE VALUES (%s, %s, %s)",
                (amc_id, name, name.lower()),
            )
        # constant series at the threshold (260 identical observations)
        _fund(cur, 900400, amc_id=_AMC_A, category=_CATS, group_key="AUDIT-400")
        _nav(cur, 900400, n=260, start=10.0)
        # constant but far below the observation threshold: not a signal
        _fund(cur, 900401, amc_id=_AMC_A, category=_CATS, group_key="AUDIT-401")
        _nav(cur, 900401, n=10, start=5.0)
        # exact duplicate pair inside one guarded family identity
        _fund(cur, 900402, amc_id=_AMC_A, category=_CATS, group_key="AUDIT-G1")
        _fund(cur, 900403, amc_id=_AMC_A, category=_CATS, group_key="AUDIT-G1")
        for code in (900402, 900403):
            _nav(cur, code, n=12, start=1.0, step=1.0)
        # identical series under a DIFFERENT AMC: must not pair
        _fund(cur, 900404, amc_id=_AMC_B, category=_CATS, group_key="AUDIT-404")
        _nav(cur, 900404, n=12, start=1.0, step=1.0)
        # identical series, same AMC, DIFFERENT category: must not pair
        _fund(cur, 900405, amc_id=_AMC_A, category="Equity Scheme - Large Cap Fund",
              group_key="AUDIT-405")
        _nav(cur, 900405, n=12, start=1.0, step=1.0)
        # terminal face-value reset: 12.0 -> 12.5 -> 10.0000
        _fund(cur, 900406, amc_id=_AMC_A, category=_CATS, group_key="AUDIT-406")
        cur.executemany(
            "INSERT INTO mf.nav_history (amfi_scheme_code, nav_date, nav) VALUES (%s,%s,%s)",
            [(900406, D0, 12.0), (900406, D0 + timedelta(days=1), 12.5),
             (900406, D0 + timedelta(days=2), 10.0)],
        )
        # terminal 10.0000 from a lower prior NAV: an early fund, not a reset
        _fund(cur, 900407, amc_id=_AMC_A, category=_CATS, group_key="AUDIT-407")
        cur.executemany(
            "INSERT INTO mf.nav_history (amfi_scheme_code, nav_date, nav) VALUES (%s,%s,%s)",
            [(900407, D0, 9.5), (900407, D0 + timedelta(days=1), 9.8),
             (900407, D0 + timedelta(days=2), 10.0)],
        )
        # ordinary accreting series: clean
        _fund(cur, 900408, amc_id=_AMC_A, category=_CATS, group_key="AUDIT-408")
        _nav(cur, 900408, n=260, start=100.0, step=0.05)

        yield {"conn": conn, "cur": cur}
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture(scope="module")
def audit_run(audit_db):
    """One full-database assessment (the same call the CLI makes)."""
    db = audit_db
    before = _nav_history_state(db["cur"])
    summary = nav_integrity.assess(db["conn"], write=True)
    after = _nav_history_state(db["cur"])
    db["nav_before"], db["nav_after"] = before, after
    db["summary"] = summary
    return db


def _assessment(db, code: int):
    row = db["cur"].execute(
        "SELECT * FROM mf.nav_quality_assessments WHERE amfi_scheme_code = %s",
        (code,),
    ).fetchone()
    return dict(row) if row else None


def test_constant_series_signal(audit_run):
    db = audit_run
    row = _assessment(db, 900400)
    assert row is not None and row["signals"] == ["constant_nav_series"]
    assert row["observation_count"] == 260
    assert row["distinct_nav_count"] == 1
    ev = row["evidence"]["constant_nav_series"]
    assert ev["value"] == "10.0000" and ev["observations"] == 260
    # below the observation threshold: no signal, but still assessed+clean
    short = _assessment(db, 900401)
    assert short is not None and short["signals"] == []
    assert short["distinct_nav_count"] == 1


def test_duplicate_signal_requires_guarded_family(audit_run):
    db = audit_run
    for code in (900402, 900403):
        row = _assessment(db, code)
        assert row is not None and row["signals"] == ["duplicate_variant_series"]
        ev = row["evidence"]["duplicate_variant_series"]
        assert ev["family"]["amc_id"] == _AMC_A
        assert ev["family"]["group_key"] == "AUDIT-G1"
    assert _assessment(db, 900402)["evidence"]["duplicate_variant_series"]["counterpart_codes"] == [900403]
    assert _assessment(db, 900403)["evidence"]["duplicate_variant_series"]["counterpart_codes"] == [900402]
    # different AMC and different category break the guarded identity
    assert _assessment(db, 900404)["signals"] == []
    assert _assessment(db, 900405)["signals"] == []
    # the accreting series is clean
    assert _assessment(db, 900408)["signals"] == []


def test_terminal_face_value_reset_detection(audit_run):
    db = audit_run
    row = _assessment(db, 900406)
    assert row["signals"] == ["terminal_face_value_reset_candidate"]
    ev = row["evidence"]["terminal_face_value_reset_candidate"]
    assert ev["last_nav"] == "10.0000" and ev["prev_nav"] == "12.5000"
    # a fund that simply ends at 10.00 from below is not a reset candidate
    assert _assessment(db, 900407)["signals"] == []


def test_summary_is_finite_sorted_json(audit_run):
    db = audit_run
    summary = db["summary"]
    blob = json.dumps(summary, sort_keys=True)
    assert "constant_nav_series" in blob
    # the fixture signals are present, with the fixture codes in the lists
    const = summary["signals"]["constant_nav_series"]
    dup = summary["signals"]["duplicate_variant_series"]
    term = summary["signals"]["terminal_face_value_reset_candidate"]
    assert 900400 in const["codes"] and 900400 not in dup["codes"]
    assert 900402 in dup["codes"] and 900403 in dup["codes"]
    assert 900406 in term["codes"]
    for item in (const, dup, term):
        assert item["codes"] == sorted(item["codes"])
        assert item["count"] == len(item["codes"])
    assert summary["affected_codes"] == sorted(summary["affected_codes"])
    assert summary["methodology_version"] == nav_integrity.METHODOLOGY_VERSION
    assert summary["schemes_assessed"] >= len(_CODES)


def test_audit_never_mutates_nav_history(audit_run):
    db = audit_run
    before, after = db["nav_before"], db["nav_after"]
    assert before == after  # (row_count, full-content md5)


def test_assessment_rows_carry_dataset_version(audit_run):
    db = audit_run
    row = _assessment(db, 900400)
    assert row["methodology_version"] == nav_integrity.METHODOLOGY_VERSION
    assert row["assessed_dataset_version"] == db["summary"]["dataset_version"]
    assert row["first_nav_date"] == D0
    assert row["last_nav_date"] == D0 + timedelta(days=259)


def test_rerun_is_idempotent(audit_run):
    db = audit_run
    first = db["summary"]
    second = nav_integrity.assess(db["conn"], write=True)
    strip = lambda d: {k: v for k, v in d.items() if k != "duration_ms"}
    assert strip(first) == strip(second)
    # and nav_history is still untouched after the second pass
    assert db["nav_after"] == _nav_history_state(db["cur"])
