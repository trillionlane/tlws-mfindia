"""Synthetic mechanics only. Real originals/DEV certification are separate gates."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import (
    hdfc_monthly,
    multi_facts,
    multi_store,
    multi_runtime,
    multi_projection,
)
from mfdataindia.enrichment import current_facts_store as current, monthly_pdf
from mfdataindia.review.app import create_amc_coverage_app, create_multi_amc_app
from mfdataindia.review.policy import fingerprint
from test_fact_verification import prepared
from test_verified_review_api import runtime_dsn
from test_verified_review_store import connection, source_state, ROLES
from test_verified_review_store import verified_db as verified_db
from test_idcw_discovery import REGISTRY

ROOT = Path(__file__).resolve().parents[1]
DETAIL = """7 | September 2026
HDFC Flexi Cap Fund
FUND MANAGER ¥ Name Since Total Exp Amit Ganatra February 01, 2026 Over 19 years
Bhagyesh Kagalkar (Gold/Silver Instruments) August 26,2026 Over 32 years
DATE OF ALLOTMENT NAV (As On SEPTEMBER 30, 2026)
ASSETS UNDER MANAGEMENT € As on September 30, 2026 Average for Month of September, 2026 ₹108,546.98Cr. ₹111,285.67Cr. QUANTITATIVE DATA
EXPENSE RATIO (As On September 30, 2026) Base expense Ratio Including statutory levies on expenses part of BER, excluding brokerage, transaction cost and execution related statutory levies^ Regular: 1.27% Direct: 0.67%
#BENCHMARK INDEX NIFTY 500 Index (TRI) ##ADDL. BENCHMARK INDEX Nifty 50 Index (TRI)
"""
NOTES = """8 | September 2026
HDFC Flexi Cap Fund
Dedicated Fund Manager for Overseas Investments: Mr. Gopal Agrawal (since September 01, 2026) (Total Experience: Over 22 years)
"""


def test_registry_is_separate_authority_bounded_and_cannot_be_overridden(tmp_path):
    r = multi_facts.registry()
    assert len(r["sources"]) == 2 and r["sources"][1]["cohort"] == [101763]
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / multi_facts.REGISTRY).write_text("{}")
    with pytest.raises(ValueError, match="registry differs"):
        multi_facts.registry(tmp_path)
    with pytest.raises(ValueError, match="registered source URL"):
        multi_runtime.fetch("https://example.com/arbitrary.pdf")


def test_private_contract_keeps_all_46_previous_paths_and_components():
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    old, new = create_amc_coverage_app(dsn).openapi(), create_multi_amc_app(dsn).openapi()
    assert len(new["paths"]) == 49
    assert all(new["paths"][k] == v for k, v in old["paths"].items())
    assert all(
        new["components"]["schemas"][k] == v for k, v in old["components"]["schemas"].items()
    )


def test_hdfc_layout_includes_footnote_and_does_not_swap_average_or_direct():
    fields, context = hdfc_monthly.fields(DETAIL, NOTES, "2026-09-30")
    assert fields["fund_managers"]["value"] == [
        "Amit Ganatra",
        "Bhagyesh Kagalkar",
        "Gopal Agrawal",
    ]
    assert fields["base_expense_ratio"]["value"] == "1.27"
    assert fields["aum"]["value"] == "108546.98"
    assert fields["risk_level"]["status"] == "unavailable" and fields["risk_level"]["value"] is None
    assert context["monthly_average_aum_not_selected"] == "111,285.67"
    assert context["fund_managers"][-1]["role"] == "dedicated_overseas_investments"


@pytest.mark.parametrize(
    "detail,notes,date",
    [
        (DETAIL.replace("Base expense Ratio", "Total expense Ratio"), NOTES, "2026-09-30"),
        (DETAIL.replace("Cr.", ""), NOTES, "2026-09-30"),
        (DETAIL, NOTES.replace("Dedicated Fund Manager", "Other Fund Manager"), "2026-09-30"),
        (DETAIL + DETAIL, NOTES, "2026-09-30"),
        (DETAIL, NOTES, "2026-10-31"),
    ],
)
def test_layout_drift_never_guesses(detail, notes, date):
    with pytest.raises(ValueError):
        hdfc_monthly.fields(detail, notes, date)


def fixture_registry(monkeypatch):
    r = deepcopy(multi_facts.registry())
    originals = {
        "edition": b"%PDF-synthetic-monthly-not-issuer",
        "identity_source": b"%PDF-synthetic-identity-not-issuer",
    }
    for role, raw in originals.items():
        r["sources"][1][role]["sha256"] = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(multi_facts, "registry", lambda root=None: deepcopy(r))
    return originals


def synthetic_extract(content, *, start, end):
    texts = [REGISTRY] if start == 1 else [DETAIL, NOTES]
    return {
        "pages": [
            {"page": n, "text": t, "text_hash": hashlib.sha256(t.encode()).hexdigest()}
            for n, t in enumerate(texts, start)
        ],
        "document_pages": 2 if start == 1 else 140,
        "source_hash": hashlib.sha256(content).hexdigest(),
        "whole_document_coverage": False,
    }


def setup(dsn, monkeypatch):
    prepared(dsn, monkeypatch)
    with connection(dsn) as conn:
        conn.execute(
            "INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES('HDFC Mutual Fund','HDFC MUTUAL FUND')"
        )
        amc = conn.execute(
            "SELECT amc_id FROM mf.amcs WHERE amfi_amc_name='HDFC Mutual Fund'"
        ).fetchone()["amc_id"]
        conn.execute(
            "INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout,isin_div_reinvest) VALUES(101763,%s,'HDFC Flexi Cap Fund','HDFC FLEXI CAP FUND','OPEN_ENDED','Equity','REGULAR','IDCW','INF179K01582','INF179K01590')",
            (amc,),
        )
        conn.execute(
            "INSERT INTO mf.fund_facts(amfi_scheme_code,source,aum,base_expense_ratio,risk_level) VALUES(101763,'MANUAL',123.45,1.37,'Very High')"
        )
        before = source_state(conn)
    originals = fixture_registry(monkeypatch)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        assert multi_store.install(conn, ROOT)["status"] == "INSTALLED"
    monkeypatch.setattr(monthly_pdf, "extract", synthetic_extract)
    archives = [
        {
            "uri": "gs://tlws_mf_data_source/official-monthly-facts/hdfc-flexi-cap/"
            + hashlib.sha256(v).hexdigest()
            + ".pdf",
            "generation": "1",
            "sha256": hashlib.sha256(v).hexdigest(),
            "size": len(v),
            "md5": "fixture",
        }
        for v in originals.values()
    ]
    return originals, archives, before


def publish(dsn, originals, archives, suffix="fixture"):
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        return multi_runtime.publish(
            conn,
            multi_facts.JOB + "-" + suffix,
            datetime(2026, 10, 17, tzinfo=timezone.utc),
            originals,
            archives,
        )


def reference(snapshot):
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "as_of": "2026-09-30",
        "values": multi_facts.source()["reviewed_values"],
    }


@pytest.mark.postgres
def test_atomic_publication_replay_recovery_and_source_preservation(verified_db, monkeypatch):
    originals, archives, before = setup(verified_db, monkeypatch)
    with connection(verified_db, readonly=True) as conn:
        pilot_before = conn.execute(
            "SELECT * FROM mf_live_facts.field_versions ORDER BY code,field,as_of"
        ).fetchall()
    first = publish(verified_db, originals, archives)
    assert first["status"] == "updated"
    assert publish(verified_db, originals, archives) == first
    assert publish(verified_db, originals, archives, "new-execution")["status"] == "unchanged"
    snapshot = hdfc_monthly.derive(
        multi_facts.source()["identity"], originals["edition"], originals["identity_source"]
    )
    assert all("text" not in page for page in snapshot["extraction"]["pages"])
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        activation = multi_projection.activate(conn, ROOT, reference(snapshot))
        assert (
            current.get(conn, 101763)["fields"]["fund_managers"]["value"]
            == multi_facts.source()["reviewed_values"]["fund_managers"]
        )
        assert (
            current.get(conn, 101763)["fields"]["fund_managers"]["verification_status"]
            == "needs_verification"
        )
        assert (
            conn.execute(
                "SELECT * FROM mf_live_facts.field_versions WHERE code<>101763 ORDER BY code,field,as_of"
            ).fetchall()
            == pilot_before
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        multi_projection.recover(conn, ROOT, activation["transition_id"])
        assert current.get(conn, 101763)["fields"]["aum"]["value"] is None
        assert (
            conn.execute(
                "SELECT * FROM mf_live_facts.field_versions ORDER BY code,field,as_of"
            ).fetchall()
            == pilot_before
        )
    with connection(verified_db) as conn:
        assert source_state(conn) == before
        assert conn.execute("SELECT count(*) AS n FROM mf_multi_facts.sources").fetchone()["n"] == 2
        assert (
            conn.execute("SELECT count(*) AS n FROM mf_multi_facts.snapshots").fetchone()["n"] == 1
        )
        assert multi_store.get(conn, 101763)["snapshots"][0]["published"]


@pytest.mark.postgres
def test_direct_snapshot_cannot_publish_and_bad_receipt_rolls_back(verified_db, monkeypatch):
    originals, archives, _ = setup(verified_db, monkeypatch)
    real = multi_store.append_run
    monkeypatch.setattr(
        multi_store,
        "append_run",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("synthetic failure after snapshot")),
    )
    with pytest.raises(ValueError):
        publish(verified_db, originals, archives)
    with connection(verified_db) as conn:
        assert conn.execute("SELECT count(*) AS n FROM mf_multi_facts.sources").fetchone()["n"] == 0
        assert (
            conn.execute("SELECT count(*) AS n FROM mf_multi_facts.snapshots").fetchone()["n"] == 0
        )
    monkeypatch.setattr(multi_store, "append_run", real)
    with pytest.raises(psycopg.errors.RaiseException, match="archive receipt"):
        publish(verified_db, originals, [{**a, "uri": "gs://wrong/object"} for a in archives])
    first = publish(verified_db, originals, archives)
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        body = {k: v for k, v in first.items() if k != "run_id"}
        body.update(execution_id=multi_facts.JOB + "-invalid", status="updated", snapshot_ids=[])
        with pytest.raises(psycopg.errors.RaiseException, match="cohort"):
            multi_store.append_run(conn, body)


@pytest.mark.postgres
def test_production_default_grants_and_runtime_raw_boundary(verified_db, monkeypatch):
    setup(verified_db, monkeypatch)
    for role in ROLES:
        with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
            multi_store.verify_acl(conn)
            assert conn.execute(
                "SELECT has_column_privilege(current_user,'mf_multi_facts.sources','content','SELECT') AS allowed"
            ).fetchone()["allowed"] == (role == ROLES[0])
    with connection(verified_db) as conn:
        for table in multi_store.TABLES | {"prior_field_versions"}:
            assert not conn.execute(
                "SELECT has_table_privilege('mfdata_ingest',%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE') AS allowed",
                ("mf_multi_facts." + table,),
            ).fetchone()["allowed"]
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM mf_multi_facts.configuration")


def test_one_adapter_failure_does_not_suppress_other_or_become_success(monkeypatch):
    calls = []

    def broken(*a, **k):
        calls.append("absl")
        raise ValueError("untrusted secret/body")

    def good(*a, **k):
        calls.append("hdfc")
        return {"status": "updated"}

    monkeypatch.setattr(multi_runtime.refresh_runtime, "execute", broken)
    monkeypatch.setattr(multi_runtime, "hdfc", good)
    result = multi_runtime.execute("fixture", multi_facts.JOB + "-fixture")
    assert calls == ["absl", "hdfc"] and result["status"] == "failed"
    assert result["sources"][1]["result"]["status"] == "updated"
    assert "untrusted" not in str(result)
    assert (
        multi_runtime.execute("fixture", multi_facts.JOB + "-fixture", task_index=0)["status"]
        == "failed"
    )
    assert (
        multi_runtime.execute("fixture", multi_facts.JOB + "-fixture", task_index=1)["status"]
        == "completed"
    )
    with pytest.raises(ValueError):
        multi_runtime.execute("fixture", multi_facts.JOB + "-fixture", task_index=2)


@pytest.mark.postgres
def test_committed_provisional_snapshot_is_not_a_current_publication(verified_db, monkeypatch):
    originals, archives, _ = setup(verified_db, monkeypatch)
    append = multi_store.append_run
    monkeypatch.setattr(
        multi_store, "append_run", lambda *a, **k: {"status": "synthetic-provisional-only"}
    )
    publish(verified_db, originals, archives)
    snapshot = hdfc_monthly.derive(
        multi_facts.source()["identity"], originals["edition"], originals["identity_source"]
    )
    with connection(verified_db) as conn:
        assert not multi_store.get(conn, 101763)["snapshots"][0]["published"]
        with pytest.raises(ValueError, match="current field differs"):
            multi_projection.activate(conn, ROOT, reference(snapshot))
        assert current.policy_document(conn)["version"] == 2
    monkeypatch.setattr(multi_store, "append_run", append)
    assert publish(verified_db, originals, archives)["status"] == "updated"


@pytest.mark.postgres
def test_changed_identity_and_forged_values_are_rejected_at_database_boundary(
    verified_db, monkeypatch
):
    originals, archives, _ = setup(verified_db, monkeypatch)
    derive = hdfc_monthly.derive

    def forged(*a, **k):
        value = derive(*a, **k)
        value["fields"]["aum"]["value"] = "999999"
        body = {k: v for k, v in value.items() if k != "snapshot_id"}
        return {**body, "snapshot_id": fingerprint(body)}

    monkeypatch.setattr(hdfc_monthly, "derive", forged)
    with pytest.raises(psycopg.errors.RaiseException, match="snapshot registered"):
        publish(verified_db, originals, archives)
    monkeypatch.setattr(hdfc_monthly, "derive", derive)
    first = publish(verified_db, originals, archives)
    with connection(verified_db) as conn:
        conn.execute(
            "UPDATE mf.funds SET scheme_name='Changed fixture only' WHERE amfi_scheme_code=101763"
        )
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        body = {k: v for k, v in first.items() if k != "run_id"}
        body.update(execution_id=multi_facts.JOB + "-identity-drift", status="unchanged")
        with pytest.raises(psycopg.errors.RaiseException, match="identity changed"):
            multi_store.append_run(conn, body)


@pytest.mark.postgres
def test_recovery_ignores_failed_candidate_graph_and_is_replayable(verified_db, monkeypatch):
    originals, archives, _ = setup(verified_db, monkeypatch)
    publish(verified_db, originals, archives)
    snapshot = hdfc_monthly.derive(
        multi_facts.source()["identity"], originals["edition"], originals["identity_source"]
    )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        activated = multi_projection.activate(conn, ROOT, reference(snapshot))
    with connection(verified_db) as conn:
        conn.execute(
            "CREATE OR REPLACE VIEW mf_live_facts.field_versions AS SELECT * FROM mf_multi_facts.prior_field_versions WHERE false"
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert (
            multi_projection.recover(conn, ROOT, activated["transition_id"])["status"]
            == "RECOVERED"
        )
        assert (
            multi_projection.recover(conn, ROOT, activated["transition_id"])["status"]
            == "ALREADY_RECOVERED"
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        multi_projection.activate(conn, ROOT, reference(snapshot))
        with pytest.raises(ValueError, match="stale recovery"):
            multi_projection.recover(conn, ROOT, activated["transition_id"])


@pytest.mark.postgres
def test_signed_private_metadata_no_raw_source_and_no_http_writer(verified_db, monkeypatch):
    from fastapi.testclient import TestClient
    from test_verified_review_auth import trust, assertion
    from mfdataindia.review.app import create_review_app

    originals, archives, _ = setup(verified_db, monkeypatch)
    publish(verified_db, originals, archives)
    key, verifier = trust()
    app = create_review_app(runtime_dsn(verified_db, ROLES[1]), verifier=verifier, multi_amc=True)
    headers = {"Authorization": "Bearer " + assertion(key, scope=["review:read"])}
    with TestClient(app) as client:
        for path in (
            "/api/enrichment/v8/policy",
            "/api/enrichment/v8/refresh/status",
            "/api/enrichment/v8/funds/101763/facts",
        ):
            assert client.get(path).status_code == 401
            value = client.get(path, headers=headers)
            assert value.status_code == 200 and value.headers["Cache-Control"] == "no-store"
            assert client.post(path, json={}, headers=headers).status_code == 405
        assert (
            client.get("/api/enrichment/v8/funds/101763/facts/raw", headers=headers).status_code
            == 404
        )
        assert (
            client.get("/api/enrichment/v8/funds/99999999/facts", headers=headers).status_code
            == 404
        )
