"""DATA-02: investigation never modifies source data; rehearsal never commits."""

from __future__ import annotations

import copy
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from mfdataindia.audit import nav_integrity as audit
from mfdataindia.audit.remediation import (
    build_register,
    export_register,
    fingerprint,
    read_document,
    record_decision,
    record_proposal,
    register_snapshots,
    seal,
)
from mfdataindia.audit.remediation_evidence import (
    archive_evidence,
    build_proposal,
    load_amfi_observations,
    nav_decimal,
    write_once,
)
from mfdataindia.audit.remediation_rehearsal import (
    SCRATCH_PREFIX,
    _trial_apply,
    _trial_restore,
    rehearse,
)
from mfdataindia.store.locks import DataOperationBusy, acquire_nav_data_operation_lock
from mfdataindia.store.postgres import PostgresStore

ROOT = Path(__file__).resolve().parents[1]
HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt=01-Jan-2026&todt=02-Jan-2026&tp=1&mc=0"


def _rows():
    family = {
        "amc_id": 1,
        "scheme_type": "OPEN_ENDED",
        "scheme_category": "Debt",
        "group_key": "PAIR",
    }
    result = []
    for code in (1, 2, 3, 4):
        signals, evidence = [], {}
        if code == 1:
            signals = [audit.SIGNAL_CONSTANT]
            evidence = {audit.SIGNAL_CONSTANT: {"value": "10.0000"}}
        elif code in (2, 3):
            signals = [audit.SIGNAL_DUPLICATE]
            evidence = {
                audit.SIGNAL_DUPLICATE: {
                    "counterpart_codes": [3 if code == 2 else 2],
                    "family": family,
                    "fingerprint": "a" * 32,
                }
            }
        result.append(
            {
                "amfi_scheme_code": code,
                "assessed_dataset_version": 8,
                "methodology_version": "1",
                "signals": signals,
                "evidence": evidence,
                "identity": {"option_type": "IDCW" if code == 3 else "GROWTH", **family},
                "observation_count": 250,
                "first_nav_date": date(2026, 1, 1),
                "last_nav_date": date(2026, 9, 8),
            }
        )
    return result


def _findings(rows):
    return fingerprint(
        {
            str(r["amfi_scheme_code"]): {"signals": sorted(r["signals"]), "evidence": r["evidence"]}
            for r in rows
            if r["signals"]
        }
    )


class _ReviewFixture(dict):
    def __repr__(self):
        # Pytest renders fixtures on failure; connection credentials must not
        # appear in captured test reports.
        return repr({k: v for k, v in self.items() if k != "dsn"})


def test_register_is_deterministic_complete_and_does_not_invent_verdicts():
    rows = _rows()
    register = build_register(
        rows, dataset_version=8, expected_findings_sha256=_findings(rows), source_manifest={}
    )
    assert register["flagged_schemes"] == 3
    assert register["duplicate_clusters"] == 1
    assert register["case_count"] == 2
    assert register["signal_counts"][audit.SIGNAL_DUPLICATE] == 2
    assert register["decisions_recorded"] == 0
    assert all(c["review_state"] == "pending_review" for c in register["cases"])
    assert (
        build_register(
            list(reversed(rows)),
            dataset_version=8,
            expected_findings_sha256=_findings(rows),
            source_manifest={},
        )
        == register
    )


@pytest.mark.parametrize(
    "failure", ["hash", "stale", "asymmetric", "missing", "duplicate", "identity"]
)
def test_register_rejects_untrusted_or_incomplete_audit(failure):
    rows = _rows()
    expected = _findings(rows)
    if failure == "hash":
        expected = "0" * 64
    elif failure == "stale":
        rows[0]["assessed_dataset_version"] = 7
    elif failure == "asymmetric":
        rows[2]["evidence"][audit.SIGNAL_DUPLICATE]["counterpart_codes"] = [4]
        expected = _findings(rows)
    elif failure == "missing":
        rows.pop(2)
        expected = _findings(rows)
    elif failure == "duplicate":
        rows.append(rows[0])
    else:
        rows[1]["identity"]["amc_id"] = 99
    with pytest.raises(ValueError):
        build_register(
            rows, dataset_version=8, expected_findings_sha256=expected, source_manifest={}
        )


@pytest.mark.parametrize(
    "value", [1.2, True, "NaN", "Infinity", "-1", "1.00001", "100000000000000"]
)
def test_replacement_nav_never_rounds_or_accepts_nonfinite_values(value):
    with pytest.raises(ValueError):
        nav_decimal(value)
    assert nav_decimal("12345678901234.1234") == Decimal("12345678901234.1234")


def _archive(tmp_path, *, values=("12.5000", "12.7000"), url=HISTORY_URL):
    path = tmp_path / "raw.txt"
    header = "Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Date\n"
    content = header + "Open Ended Schemes ( Debt Scheme - Liquid Fund )\nTest AMC\n"
    for index, value in enumerate(values, start=1):
        content += f"4;Test Fund;Regular;Growth;;;{value};0{index}-Jan-2026\n"
    path.write_text(content)
    archive = tmp_path / "archive"
    ref = archive_evidence(
        path, archive, source="AMFI_NAV_HISTORY", url=url, fetched_at="2026-01-03T00:00:00+00:00"
    )
    return ref, archive


def test_archive_preserves_exact_published_decimal_and_detects_tampering(tmp_path):
    ref, archive = _archive(tmp_path, values=("12345678901234.1234", "12.7000"))
    assert load_amfi_observations(ref, archive)[(4, date(2026, 1, 1))] == Decimal(
        "12345678901234.1234"
    )
    with pytest.raises(ValueError, match="different content"):
        write_once(archive / f"{ref['raw_sha256']}.bin", b"modified")
    (archive / f"{ref['raw_sha256']}.bin").write_bytes(b"modified")
    with pytest.raises(ValueError, match="changed"):
        load_amfi_observations(ref, archive)


@pytest.mark.parametrize(
    "url",
    [
        "http://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx",
        "https://portal.amfiindia.com.evil.invalid/DownloadNAVHistoryReport_Po.aspx",
        "https://user@portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx",
    ],
)
def test_evidence_source_rejects_impersonated_authority(tmp_path, url):
    with pytest.raises(ValueError):
        _archive(tmp_path, url=url)


def test_ambiguous_source_rows_and_duplicate_json_keys_fail(tmp_path):
    ref, archive = _archive(tmp_path)
    path = tmp_path / "raw.txt"
    path.write_text(path.read_text() + "4;Test Fund;Regular;Growth;;;99.0000;02-Jan-2026\n")
    ref = archive_evidence(
        path,
        archive,
        source="AMFI_NAV_HISTORY",
        url=HISTORY_URL,
        fetched_at="2026-01-03T00:00:00+00:00",
    )
    with pytest.raises(ValueError, match="conflicting"):
        load_amfi_observations(ref, archive)
    path.write_text('{"sha256":"a","sha256":"b"}')
    with pytest.raises(ValueError, match="duplicate JSON"):
        read_document(path)


def test_cli_has_no_live_correction_action():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/review_nav_remediation.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "record-proposal" in result.stdout and "rehearse" in result.stdout
    invalid = subprocess.run(
        [sys.executable, str(ROOT / "scripts/review_nav_remediation.py"), "apply"],
        capture_output=True,
        text=True,
    )
    assert invalid.returncode != 0


@pytest.fixture(scope="module")
def review_db(pg_dsn):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    name = SCRATCH_PREFIX + uuid4().hex[:12]
    dsn = make_conninfo(pg_dsn, dbname=name)
    admin = psycopg.connect(pg_dsn, autocommit=True)
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    role = "mfdata_remediation_review"
    create_role = not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
    if not create_role:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        admin.close()
        pytest.skip("review role already exists; refusing to alter shared cluster privileges")
    elevated_role = "remediation_elevated_" + uuid4().hex[:12]
    if create_role:
        admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(elevated_role)))
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN INHERIT CREATEDB CREATEROLE REPLICATION BYPASSRLS"
            ).format(sql.Identifier(role))
        )
        admin.execute(
            sql.SQL("GRANT {} TO {}").format(sql.Identifier(elevated_role), sql.Identifier(role))
        )
    try:
        with PostgresStore(dsn, use_copy=False) as store:
            store.apply_migrations(ROOT / "sql")
            with store.transaction() as conn:
                conn.execute(
                    "INSERT INTO mf.amcs (amfi_amc_name,normalised_name) VALUES ('Review AMC','REVIEW AMC')"
                )
                for code in range(1, 6):
                    conn.execute(
                        "INSERT INTO mf.funds (amfi_scheme_code,scheme_name,scheme_name_norm,amc_id,"
                        "scheme_type,scheme_category,plan_type,option_type) "
                        "VALUES (%s,%s,%s,1,'OPEN_ENDED','Debt','REGULAR',%s)",
                        (code, f"Test {code}", f"TEST {code}", "IDCW" if code == 3 else "GROWTH"),
                    )
                    conn.execute(
                        "INSERT INTO mf.fund_variants (amfi_scheme_code,group_key,base_scheme_name) "
                        "VALUES (%s,%s,'Test')",
                        (code, "PAIR" if code in (2, 3) else str(code)),
                    )
                    values = (
                        [Decimal("10.0000")] * 250
                        if code == 1
                        else [Decimal("10"), Decimal("11"), Decimal("12"), Decimal("13")]
                        if code in (2, 3)
                        else [Decimal("12.5"), Decimal("10")]
                        if code == 4
                        else [Decimal("100"), Decimal("101")]
                    )
                    with conn.cursor() as cur:
                        cur.executemany(
                            "INSERT INTO mf.nav_history (amfi_scheme_code,nav_date,nav) VALUES (%s,%s,%s)",
                            [
                                (code, date(2026, 1, 1) + timedelta(days=i), v)
                                for i, v in enumerate(values)
                            ],
                        )
                conn.execute("SELECT mf.refresh_dataset_summary('review_fixture')")
                summary = audit.assess(conn)
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            register = export_register(
                conn,
                expected_dataset_version=summary["dataset_version"],
                expected_findings_sha256=summary["findings_sha256"],
            )
        yield _ReviewFixture(
            dsn=dsn,
            name=name,
            register=register,
            owned_role=create_role,
            elevated_role=elevated_role,
        )
    finally:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s", (name,)
        )
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        if create_role:
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(elevated_role)))
        admin.close()


def _proposal(review_db, tmp_path):
    ref, archive = _archive(tmp_path)
    case = next(
        c for c in review_db["register"]["cases"] if c["signal"] == audit.SIGNAL_TERMINAL_RESET
    )
    decision = seal(
        {
            "case_snapshot_sha256": case["sha256"],
            "supersedes_sha256": None,
            "disposition": "confirmed_nav_discrepancy",
            "reviewer_reference": "FIXTURE-REVIEW",
            "reason": "Synthetic authoritative replacement for rollback rehearsal",
            "evidence": [ref],
        }
    )
    with psycopg.connect(review_db["dsn"], row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        plan = build_proposal(
            conn,
            case=case,
            decision=decision,
            reference=ref,
            archive=archive,
            expected_dataset_version=review_db["register"]["dataset_version"],
        )
    return case, decision, plan, archive


def _source_state(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        nav = conn.execute(
            "SELECT * FROM mf.nav_history ORDER BY amfi_scheme_code,nav_date"
        ).fetchall()
        version = conn.execute("SELECT * FROM mf.dataset_summary WHERE singleton").fetchone()
        funds = conn.execute("SELECT * FROM mf.funds ORDER BY amfi_scheme_code").fetchall()
        assessments = conn.execute(
            "SELECT * FROM mf.nav_quality_assessments ORDER BY amfi_scheme_code"
        ).fetchall()
    return fingerprint([nav, version, funds, assessments])


@pytest.mark.postgres
def test_export_is_read_only_current_and_groups_actual_audit(review_db):
    baseline = _source_state(review_db["dsn"])
    assert review_db["register"]["flagged_schemes"] == 4
    assert review_db["register"]["case_count"] == 3
    with psycopg.connect(review_db["dsn"], row_factory=dict_row) as conn:
        export_register(
            conn,
            expected_dataset_version=review_db["register"]["dataset_version"],
            expected_findings_sha256=review_db["register"]["findings_sha256"],
        )
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("UPDATE mf.nav_history SET nav=99 WHERE amfi_scheme_code=4")
    assert _source_state(review_db["dsn"]) == baseline


@pytest.mark.postgres
def test_review_ledger_is_append_only_and_decisions_do_not_clear_signals(review_db, tmp_path):
    baseline = _source_state(review_db["dsn"])
    case, decision, plan, archive = _proposal(review_db, tmp_path)
    with psycopg.connect(review_db["dsn"], row_factory=dict_row) as conn:
        assert register_snapshots(conn, review_db["register"]) == 3
        assert register_snapshots(conn, review_db["register"]) == 0
        assert record_decision(conn, decision) == "RECORDED"
        assert record_decision(conn, decision) == "ALREADY_RECORDED"
        record_proposal(conn, plan)
        outside = copy.deepcopy(plan)
        del outside["sha256"]
        outside["changes"][0]["amfi_scheme_code"] = 5
        with pytest.raises(ValueError, match="outside the reviewed case"):
            record_proposal(conn, seal(outside))
        revised = seal(
            {k: v for k, v in decision.items() if k != "sha256"}
            | {
                "supersedes_sha256": decision["sha256"],
                "disposition": "insufficient_evidence",
                "reason": "Reopened",
            }
        )
        assert record_decision(conn, revised) == "RECORDED"
        with pytest.raises(ValueError, match="recorded discrepancy"):
            record_proposal(conn, plan)
        with pytest.raises(ValueError, match="chain changed"):
            record_decision(
                conn,
                seal(
                    {k: v for k, v in decision.items() if k != "sha256"}
                    | {"reason": "Stale review"}
                ),
            )
    for table in (
        "nav_remediation_snapshots",
        "nav_remediation_decisions",
        "nav_remediation_proposals",
    ):
        for command in (
            f"DELETE FROM mf.{table}",
            f"TRUNCATE mf.{table} CASCADE",
            f"UPDATE mf.{table} SET recorded_at=now()",
        ):
            with psycopg.connect(review_db["dsn"]) as conn:
                with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                    conn.execute(command)
    assert _source_state(review_db["dsn"]) == baseline


@pytest.mark.postgres
def test_rehearsal_replays_restores_and_never_commits(review_db, tmp_path):
    baseline = _source_state(review_db["dsn"])
    _, _, plan, archive = _proposal(review_db, tmp_path)
    result = rehearse(review_db["dsn"], plan, archive, confirm_scratch_database=review_db["name"])
    assert result["status"] == "SCRATCH_REHEARSAL_PASSED_AND_ROLLED_BACK"
    assert result["baseline_sha256"] == result["restored_sha256"]
    assert _source_state(review_db["dsn"]) == baseline
    with pytest.raises(RuntimeError, match="interrupted"):
        rehearse(
            review_db["dsn"],
            plan,
            archive,
            confirm_scratch_database=review_db["name"],
            simulate_failure_after=1,
        )
    assert _source_state(review_db["dsn"]) == baseline
    with pytest.raises(ValueError, match="scratch"):
        rehearse(review_db["dsn"], plan, archive, confirm_scratch_database="mfdataindia")
    with pytest.raises(ValueError, match="differs"):
        rehearse(review_db["dsn"], plan, archive, confirm_scratch_database=SCRATCH_PREFIX + "wrong")


@pytest.mark.postgres
def test_rehearsal_rejects_drift_and_tampered_proposals(review_db, tmp_path):
    baseline = _source_state(review_db["dsn"])
    _, _, plan, archive = _proposal(review_db, tmp_path)
    for alteration in ("before", "after", "version"):
        bad = copy.deepcopy(plan)
        del bad["sha256"]
        if alteration == "before":
            bad["changes"][0]["before"]["nav"] = "9.0000"
        elif alteration == "after":
            bad["changes"][0]["after_nav"] = "99.0000"
        else:
            bad["expected_dataset_version"] += 1
        with pytest.raises(ValueError):
            rehearse(
                review_db["dsn"], seal(bad), archive, confirm_scratch_database=review_db["name"]
            )
    assert _source_state(review_db["dsn"]) == baseline


@pytest.mark.postgres
def test_conditional_rollback_rejects_later_changes_and_lock_contention(review_db, tmp_path):
    baseline = _source_state(review_db["dsn"])
    _, _, plan, archive = _proposal(review_db, tmp_path)
    with psycopg.connect(review_db["dsn"], row_factory=dict_row) as conn:
        after = _trial_apply(conn, plan["changes"])
        conn.execute(
            "UPDATE mf.nav_history SET nav=88 WHERE amfi_scheme_code=4 AND nav_date='2026-01-02'"
        )
        with pytest.raises(ValueError, match="later observation"):
            _trial_restore(conn, plan["changes"], after)
        assert conn.execute(
            "SELECT nav FROM mf.nav_history WHERE amfi_scheme_code=4 AND nav_date='2026-01-02'"
        ).fetchone()["nav"] == Decimal("88")
        conn.rollback()
    with psycopg.connect(review_db["dsn"], row_factory=dict_row) as holder:
        acquire_nav_data_operation_lock(holder)
        with pytest.raises(DataOperationBusy):
            rehearse(review_db["dsn"], plan, archive, confirm_scratch_database=review_db["name"])
    assert _source_state(review_db["dsn"]) == baseline


@pytest.mark.postgres
def test_review_role_has_no_source_or_rewrite_privileges(review_db):
    if not review_db["owned_role"]:
        pytest.skip("review role preexists; do not claim to certify external privileges")
    with psycopg.connect(review_db["dsn"], row_factory=dict_row) as conn:
        conn.execute("SET LOCAL ROLE mfdata_remediation_review")
        assert (
            conn.execute("SELECT count(*) AS n FROM mf.nav_remediation_snapshots").fetchone()["n"]
            >= 0
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE mf.nav_history SET nav=99 WHERE amfi_scheme_code=4")
    with psycopg.connect(review_db["dsn"], row_factory=dict_row) as conn:
        for table in (
            "nav_history",
            "funds",
            "fund_family",
            "dataset_summary",
            "nav_quality_assessments",
        ):
            assert (
                conn.execute(
                    "SELECT has_table_privilege('mfdata_remediation_review', %s, 'INSERT,UPDATE,DELETE,TRUNCATE') AS allowed",
                    (f"mf.{table}",),
                ).fetchone()["allowed"]
                is False
            )
        attributes = conn.execute(
            "SELECT rolinherit, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname='mfdata_remediation_review'"
        ).fetchone()
        assert not any(attributes.values())
        assert (
            conn.execute(
                "SELECT pg_has_role('mfdata_remediation_review', %s, 'member') AS member",
                (review_db["elevated_role"],),
            ).fetchone()["member"]
            is False
        )


@pytest.mark.postgres
def test_review_role_can_append_without_update_privileges(review_db):
    baseline = _source_state(review_db["dsn"])
    case = review_db["register"]["cases"][0]
    with psycopg.connect(review_db["dsn"], row_factory=dict_row) as conn:
        conn.execute("SET LOCAL ROLE mfdata_remediation_review")
        register_snapshots(conn, review_db["register"])
        latest = conn.execute(
            "SELECT d.decision_sha256 FROM mf.nav_remediation_decisions d "
            "WHERE snapshot_sha256=%s AND NOT EXISTS "
            "(SELECT 1 FROM mf.nav_remediation_decisions n WHERE n.supersedes_sha256=d.decision_sha256)",
            (case["sha256"],),
        ).fetchone()
        decision = seal(
            {
                "case_snapshot_sha256": case["sha256"],
                "supersedes_sha256": latest["decision_sha256"] if latest else None,
                "disposition": "insufficient_evidence",
                "reason": "Permission test review",
                "reviewer_reference": "FIXTURE-REVIEW-ROLE",
                "evidence": [],
            }
        )
        assert record_decision(conn, decision) == "RECORDED"
    assert _source_state(review_db["dsn"]) == baseline


@pytest.mark.postgres
def test_review_appending_fails_immediately_on_parallel_reviewer(review_db):
    case = review_db["register"]["cases"][0]
    decision = seal(
        {
            "case_snapshot_sha256": case["sha256"],
            "supersedes_sha256": None,
            "disposition": "insufficient_evidence",
            "reason": "Parallel review",
            "reviewer_reference": "FIXTURE-PARALLEL",
            "evidence": [],
        }
    )
    with psycopg.connect(review_db["dsn"]) as holder:
        holder.execute("SELECT pg_advisory_xact_lock(hashtext(%s), 2)", (case["sha256"],))
        with psycopg.connect(review_db["dsn"], row_factory=dict_row) as other:
            with pytest.raises(ValueError, match="another reviewer"):
                record_decision(other, decision)
