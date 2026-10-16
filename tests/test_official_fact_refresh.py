"""Synthetic DB publication/recovery proofs; never live deployment acceptance."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

from mfdataindia.api import queries
from mfdataindia.enrichment import current_facts_store as current, monthly_store, monthly_pdf
from mfdataindia.enrichment import monthly_projection as projection, refresh_store, refresh_policy
from mfdataindia.review.policy import fingerprint
from test_monthly_facts import prepare_monthly, CHANGES
from test_official_facts import DETAIL
from test_verified_review_api import runtime_dsn
from test_verified_review_store import ROLES, connection, source_state
from test_verified_review_store import verified_db as verified_db
from mfdataindia.enrichment import refresh_acquisition as acquisition, refresh_runtime
from mfdataindia.enrichment.refresh_certification import check_prior_state
from mfdataindia.review.app import create_monthly_facts_app, create_fact_refresh_app

ROOT = Path(__file__).resolve().parents[1]


def test_refresh_contract_preserves_all_previous_routes_and_schemas():
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    old, new = create_monthly_facts_app(dsn).openapi(), create_fact_refresh_app(dsn).openapi()
    assert len(old["paths"]) == 37 and len(new["paths"]) == 39
    assert all(new["paths"][k] == v for k, v in old["paths"].items())
    assert all(
        new["components"]["schemas"][k] == v for k, v in old["components"]["schemas"].items()
    )


def test_discovery_retains_catalogs_and_selects_latest_without_future_or_duplicate_guessing():
    import json

    landing = b"var factsheetDatasourceId = '{00000000-0000-0000-0000-000000000001}';"
    calls = []

    def transport(url, **kwargs):
        calls.append(url)
        content = (
            json.dumps(
                {
                    "ReturnCode": 1,
                    "MonthlyFactsheets": [
                        {
                            "DocumentTitle": "Empower for the Month of October 2026",
                            "DocumentLink": "/-/media/bsl/files/resources/factsheets/2026/oct.pdf",
                        }
                    ],
                }
            ).encode()
            if kwargs
            else landing
        )
        return {
            "source_url": url,
            "content": content,
            "media_type": "application/json" if kwargs else "text/html",
        }

    catalogs, document = acquisition.discover(
        datetime(2026, 10, 16, tzinfo=timezone.utc), transport=transport
    )
    assert len(catalogs) == 2 and len(calls) == 2 and document.period == "October 2026"
    with pytest.raises(ValueError, match="future"):
        acquisition.discover(datetime(2026, 9, 16, tzinfo=timezone.utc), transport=transport)


def test_bounded_page_discovery_uses_explicit_date_and_rejects_ambiguous_layout(monkeypatch):
    detail = DETAIL.replace(
        "Aditya Birla Sun Life Large Cap Fund", "ADITYA BIRLA SUN LIFE LARGE CAP FUND"
    )
    pages = [{"page": n, "text": ""} for n in range(1, 25)]
    pages[13]["text"] = detail
    pages[14]["text"] = "Aditya Birla Sun Life Large Cap Fund\nChanges"
    monkeypatch.setattr(monthly_pdf, "extract", lambda *a, **k: {"pages": pages})
    assert acquisition.pages(b"%PDF-fixture") == (14, 15, __import__("datetime").date(2026, 8, 31))
    pages[19]["text"] = detail
    pages[20]["text"] = pages[14]["text"]
    with pytest.raises(ValueError, match="ambiguous"):
        acquisition.pages(b"%PDF-fixture")


@pytest.mark.postgres
def test_failed_acquisition_receipt_is_replayable_and_cannot_advance_current(
    verified_db, monkeypatch
):
    setup(verified_db, monkeypatch)
    dsn = runtime_dsn(verified_db, ROLES[0])
    calls = []

    def broken(*a, **k):
        calls.append(1)
        raise ValueError("untrusted body must not enter stored error")

    execution = refresh_runtime.JOB + "-fixture"
    result = refresh_runtime.execute(dsn, execution, transport=broken)
    assert result["status"] == "failed" and result["phase"] == "discovery"
    assert "untrusted" not in str(result)
    assert refresh_runtime.execute(dsn, execution, transport=broken) == result
    assert calls == [1]
    with connection(verified_db, readonly=True) as conn:
        assert not current.projection_state(conn)
        assert refresh_store.capture(conn)["publications"]["rows"] == 0
        assert monthly_store.capture(conn)["snapshots"]["rows"] == 0


@pytest.mark.postgres
@pytest.mark.parametrize("fail_after_append", [False, True])
def test_runtime_archives_before_atomic_pair_and_replay_never_rewrites(
    verified_db, monkeypatch, fail_after_append
):
    selected, raw, _ = setup(verified_db, monkeypatch)
    monkeypatch.setattr(refresh_runtime, "IDENTITY_DOCUMENT", selected.identity_document_id)
    catalogs = [
        {
            "source_url": "https://mutualfund.adityabirlacapital.com/forms-and-downloads/factsheets",
            "media_type": "text/html",
            "content": b"official landing",
        },
        {
            "source_url": "https://mutualfund.adityabirlacapital.com/api/sitecore/CalculatorPage/GetMonthlyFactsheetsByYear?year=2026",
            "media_type": "application/json",
            "content": b'{"catalog":"fixture"}',
        },
    ]
    document = SimpleNamespace(url=selected.intake.final_url, period="September 2026")
    monkeypatch.setattr(acquisition, "discover", lambda *a, **k: (catalogs, document))
    monkeypatch.setattr(
        acquisition, "pages", lambda *a: (15, 16, __import__("datetime").date(2026, 8, 31))
    )
    calls = []

    def archive(session, content):
        assert content == raw
        calls.append("archive")
        return {
            "uri": "gs://fixture/original.pdf",
            "generation": "1",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        }

    original = monthly_store.publish

    def append(*a, **k):
        assert calls == ["archive"]
        calls.append("append")
        result = original(*a, **k)
        if fail_after_append:
            raise ValueError("controlled post-append failure")
        return result

    monkeypatch.setattr(monthly_store, "publish", append)
    dsn = runtime_dsn(verified_db, ROLES[0])
    execution = refresh_runtime.JOB + "-atomic"
    result = refresh_runtime.execute(
        dsn, execution, transport=lambda *a, **k: {"content": raw}, archive=archive
    )
    assert calls == ["archive", "append"]
    assert result["status"] == ("failed" if fail_after_append else "completed_with_field_gaps")
    assert (
        refresh_runtime.execute(
            dsn,
            execution,
            transport=lambda *a, **k: pytest.fail("no replay acquisition"),
            archive=archive,
        )
        == result
    )
    with connection(verified_db, readonly=True) as conn:
        state = monthly_store.capture(conn)
        assert state["sources"]["rows"] == (0 if fail_after_append else 1)
        assert state["snapshots"]["rows"] == (0 if fail_after_append else 2)
        state = refresh_store.capture(conn)
        assert state["runs"]["rows"] == 1
        assert state["publications"]["rows"] == (0 if fail_after_append else 1)
        assert state["catalogs"]["rows"] == (0 if fail_after_append else 2)


def setup(dsn, monkeypatch):
    selection, raw = prepare_monthly(dsn, monkeypatch)
    with connection(dsn) as conn:
        for code in (103173, 103174):
            conn.execute(
                "INSERT INTO mf.fund_variants(amfi_scheme_code,group_key,base_scheme_name) VALUES(%s,%s,'Large cap')",
                (code, str(code)),
            )
            conn.execute(
                "INSERT INTO mf.fund_facts(amfi_scheme_code,source,aum,base_expense_ratio,risk_level) VALUES(%s,'MANUAL',123.45,2.22,'HIGH')",
                (code,),
            )
    with connection(dsn, readonly=True) as conn:
        before = source_state(conn)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        current.install(conn, ROOT)
        assert refresh_store.install(conn, ROOT)["status"] == "INSTALLED"
    return selection, raw, before


def append_period(
    dsn, monkeypatch, selection, *, month="September", as_of="2026-09-30", ber="1.36", aum=True
):
    raw = b"%PDF-refresh-fixture-" + (as_of + ber + str(aum)).encode()
    intake = selection.intake.model_copy(
        update={
            "document_as_of": __import__("datetime").date.fromisoformat(as_of),
            "expected_sha256": hashlib.sha256(raw).hexdigest(),
        }
    )
    updated = selection.model_copy(update={"intake": intake})
    detail = (
        DETAIL.replace("August 31, 2026", month + " " + as_of[-2:] + ", 2026")
        .replace("29,710.82\n", "29,710.82 Cr\n" if aum else "29,710.82\n")
        .replace("1.35%", ber + "%")
    )
    changes = CHANGES.replace("August 2026", month + " 2026").replace(
        "1.30% 1.35%", "1.35% " + ber + "%"
    )
    monkeypatch.setattr(
        monthly_pdf,
        "extract",
        lambda *args, **kwargs: {
            "policy_hash": "4" * 64,
            "extractor_version": "synthetic",
            "engine_hash": "5" * 64,
            "pages": [
                {"page": 15, "text": detail, "text_hash": "6" * 64},
                {"page": 16, "text": changes, "text_hash": "7" * 64},
            ],
        },
    )
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        snapshots = monthly_store.publish(conn, updated, raw)
    return snapshots


def receipt(snapshots, execution, *, status="updated", mode="retained_bootstrap", catalog_ids=None):
    return {
        "format": "official-facts-refresh-run-v1",
        "mode": mode,
        "execution_id": execution,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "policy_hash": fingerprint(refresh_policy.policy()),
        "content_hash": snapshots[0]["content_hash"],
        "disclosure_as_of": snapshots[0]["disclosure_as_of"],
        "snapshot_ids": [s["snapshot_id"] for s in snapshots],
        "catalog_ids": catalog_ids or [],
        "source_rows_changed": 0,
    }


def references(snapshots):
    return {
        str(s["code"]): {
            "snapshot_id": s["snapshot_id"],
            "as_of": s["disclosure_as_of"],
            "values": {
                field: s["fields"][field]["value"]
                for field in (
                    "benchmark_name",
                    "fund_managers",
                    "base_expense_ratio",
                    "risk_level",
                    "aum",
                )
            },
        }
        for s in snapshots
    }


@pytest.mark.postgres
def test_monthly_current_retains_missing_fields_old_dates_and_conflict_previous_value(
    verified_db, monkeypatch
):
    selected, _, before = setup(verified_db, monkeypatch)
    september = append_period(verified_db, monkeypatch, selected)
    with connection(verified_db) as conn:
        refresh_store.append_run(conn, receipt(september, "september"))
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        projection.activate(conn, ROOT, legacy_snapshot_ids=[], reviewed=references(september))
    with psycopg.connect(runtime_dsn(verified_db, ROLES[1]), row_factory=dict_row) as conn:
        assert (
            projection.check_current(conn, references(september), core_queries=False)["103174"][
                "aum"
            ]
            == 29710.82
        )
    for suffix, ber in [("a", "1.25"), ("b", "1.27")]:
        october = append_period(
            verified_db,
            monkeypatch,
            selected,
            month="October",
            as_of="2026-10-31",
            ber=ber,
            aum=False,
        )
        with connection(verified_db) as conn:
            refresh_store.append_run(
                conn, receipt(october, "october-" + suffix, status="completed_with_field_gaps")
            )
    with psycopg.connect(runtime_dsn(verified_db, ROLES[1]), row_factory=dict_row) as conn:
        doc = current.get(conn, 103174)
        assert doc["fields"]["base_expense_ratio"]["state"] == "conflicting"
        assert doc["fields"]["base_expense_ratio"]["value"] == "1.36"
        assert doc["fields"]["base_expense_ratio"]["disclosure_as_of"] == "2026-09-30"
        assert doc["fields"]["base_expense_ratio"]["latest_disclosure_as_of"] == "2026-10-31"
        assert doc["fields"]["aum"]["state"] == "unusable"
        assert doc["fields"]["aum"]["value"] == "29710.82"
        assert doc["fields"]["aum"]["disclosure_as_of"] == "2026-09-30"
        assert doc["fields"]["aum"]["date_basis"] == "explicit_field_date"
        assert monthly_store.get(conn, 103174, snapshot_id=september[1]["snapshot_id"])[
            "current_core_projection_includes_monthly_snapshot"
        ]
    with connection(verified_db, readonly=True) as conn:
        assert queries.fund_detail(conn, 103174)["facts"]["base_expense_ratio"] == 1.36
        assert queries.fund_detail(conn, 103174)["facts"]["aum"] == 29710.82
        assert source_state(conn) == before


@pytest.mark.postgres
def test_complete_publication_required_for_core_and_recovery_restores_previous_graph(
    verified_db, monkeypatch, tmp_path
):
    selected, _, before = setup(verified_db, monkeypatch)
    snapshots = append_period(verified_db, monkeypatch, selected)
    with connection(verified_db) as conn:
        body = receipt(snapshots, "fixture-partial")
        body["snapshot_ids"] = body["snapshot_ids"][:1]
        with pytest.raises(psycopg.errors.RaiseException, match="complete two-variant"):
            refresh_store.append_run(conn, body)
    with connection(verified_db, readonly=True) as conn:
        assert refresh_store.capture(conn)["publications"]["rows"] == 0
    # Without a publication receipt, even valid provisional source snapshots do
    # not satisfy the independent current references, and activation is atomic.
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        with pytest.raises(ValueError, match="differs from reviewed"):
            projection.activate(conn, ROOT, legacy_snapshot_ids=[], reviewed=references(snapshots))
    with connection(verified_db, readonly=True) as conn:
        assert current.configuration(conn)["generation"] == 1
        assert refresh_store.capture(conn)["transitions"]["rows"] == 0
        prior_state = {
            "policy_hash": current.configuration(conn)["policy_hash"],
            "projection_active": current.projection_state(conn),
            "view_hashes": current.capture(conn)["view_hashes"],
        }
        check_prior_state(conn, prior_state)
    with connection(verified_db) as conn:
        refresh_store.append_run(conn, receipt(snapshots, "fixture-bootstrap"))
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        activated = projection.activate(
            conn, ROOT, legacy_snapshot_ids=[], reviewed=references(snapshots)
        )
    with connection(verified_db, readonly=True) as conn:
        assert queries.fund_detail(conn, 103174)["facts"]["base_expense_ratio"] == 1.36
        with pytest.raises(ValueError, match="not restored"):
            check_prior_state(conn, prior_state)
    # A missing original SQL pin rolls back ALL partial view replacements, not
    # just the configuration row; the active graph remains fully readable.
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        with pytest.raises(FileNotFoundError):
            projection.recover(conn, activated["transition_id"], root=tmp_path)
    with connection(verified_db, readonly=True) as conn:
        current.verify_acl(conn)
        assert current.configuration(conn)["generation"] == 2
        assert queries.fund_detail(conn, 103174)["facts"]["risk_level"] == "Very High"
        assert current.get(conn, 103174)["fields"]["aum"]["value"] == "29710.82"
        assert source_state(conn) == before
    # A newer provisional append alone cannot silently advance current heads.
    later = append_period(
        verified_db, monkeypatch, selected, month="October", as_of="2026-10-31", ber="1.25"
    )
    assert later
    with connection(verified_db, readonly=True) as conn:
        assert queries.fund_detail(conn, 103174)["facts"]["base_expense_ratio"] == 1.36
    # Candidate drift must not gate restoration of the sealed prior graph.
    with connection(verified_db) as conn:
        conn.execute(
            "CREATE OR REPLACE VIEW mf_live_facts.current_values AS SELECT amfi_scheme_code AS code,'{}'::jsonb AS values FROM mf.fund_facts WHERE false"
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert (
            projection.recover(conn, activated["transition_id"], root=ROOT)["status"] == "RECOVERED"
        )
    with connection(verified_db, readonly=True) as conn:
        assert queries.fund_detail(conn, 103174)["facts"]["base_expense_ratio"] == 2.22
        assert current.configuration(conn)["generation"] == 3
        current.verify_acl(conn)
        check_prior_state(conn, prior_state)
        assert (
            current.get(conn, 103174)["policy_hash"] == current.configuration(conn)["policy_hash"]
        )
        assert source_state(conn) == before
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute(
            "CREATE OR REPLACE VIEW mf_live_facts.current_values AS SELECT amfi_scheme_code AS code,'{}'::jsonb AS values FROM mf.fund_facts WHERE false"
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert (
            projection.recover(conn, activated["transition_id"], root=ROOT)["status"]
            == "ALREADY_RECOVERED"
        )
        current.verify_acl(conn)


@pytest.mark.postgres
def test_generator_cannot_forge_bootstrap_or_write_publication_and_can_publish_complete_run(
    verified_db, monkeypatch
):
    selected, _, _ = setup(verified_db, monkeypatch)
    snapshots = append_period(verified_db, monkeypatch, selected)
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="direct owner bootstrap"):
            refresh_store.append_run(conn, receipt(snapshots, "forged-owner"))
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO mf_fact_refresh.publications VALUES('x','y',current_date,'[]')"
            )
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        catalog_ids = [
            refresh_store.append_catalog(
                conn, "https://mutualfund.adityabirlacapital.com/" + name, media, raw
            )
            for name, media, raw in [
                ("forms-and-downloads/factsheets", "text/html", b"<html>fixture</html>"),
                (
                    "api/sitecore/CalculatorPage/GetMonthlyFactsheetsByYear",
                    "application/json",
                    b'{"fixture":true}',
                ),
            ]
        ]
        body = receipt(
            snapshots, "scheduled-fixture", mode="scheduled_refresh", catalog_ids=catalog_ids
        )
        first = refresh_store.append_run(conn, body)
        assert refresh_store.append_run(conn, body) == first
    with connection(verified_db, readonly=True) as conn:
        assert refresh_store.capture(conn)["publications"]["rows"] == 1
        for table in refresh_store.TABLES:
            assert not conn.execute(
                "SELECT has_table_privilege('mfdata_ingest',%s,'SELECT,INSERT,UPDATE,DELETE') AS allowed",
                ("mf_fact_refresh." + table,),
            ).fetchone()["allowed"]
    with psycopg.connect(runtime_dsn(verified_db, ROLES[1]), row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        assert refresh_store.status(conn)["last_run"]["execution_id"] == "scheduled-fixture"
