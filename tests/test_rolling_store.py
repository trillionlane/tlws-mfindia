from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from mfdataindia.analytics import rolling, rolling_operations as operations
from mfdataindia.analytics import rolling_schemas as schemas, rolling_store as store
from mfdataindia.analytics.operations import capture_calendar, install as install_calendar
from mfdataindia.review import store as review
from mfdataindia.review.app import create_calendar_app, create_rolling_app
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.publication_operations import capture_review
from mfdataindia.review.rollout import seal_evidence
from conftest import certify_comparison_fixture
from test_calendar_analytics import sample
from test_rolling_analytics import series
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import assertion, trust
from test_verified_review_store import ROLES, connection, generate, source_state
from test_verified_review_store import verified_db as _original_verified_db
from test_verified_review_rollout import manifest

ROOT = Path(__file__).resolve().parents[1]
original_verified_db = _original_verified_db


@pytest.fixture
def rolling_db(original_verified_db):
    with connection(original_verified_db) as conn:
        day = conn.execute("SELECT max(nav_date) AS day FROM mf.nav_history").fetchone()[
            "day"
        ] + timedelta(days=1)
        for i in range(180):
            while day.weekday() >= 5:
                day += timedelta(days=1)
            for code in (1, 2):
                conn.execute(
                    "INSERT INTO mf.nav_history(amfi_scheme_code,nav_date,nav) VALUES(%s,%s,%s)",
                    (code, day, 100 + i % 5 if code == 1 else 80 + i % 7),
                )
            day += timedelta(days=1)
        certify_comparison_fixture(conn)
    with psycopg.connect(original_verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        install_calendar(conn, ROOT)
    yield original_verified_db


def installed(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        return operations.install(conn, ROOT)


def generated(dsn, code=1):
    with connection(dsn, readonly=True) as conn:
        snapshot = review.capture(conn, code)
    with connection(dsn, ROLES[0]) as conn:
        return store.publish(conn, [snapshot])[0]


def certificate(result):
    m = manifest().model_copy(update={"cohort": [result["code"]]})
    value = seal_evidence(
        m,
        "rolling-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "source_writes": 0,
            "policy_hash": result["policy_hash"],
            "origin": "https://fixture.a.run.app",
            "audience": "https://fixture.a.run.app",
            "cohort": [
                {
                    "code": result["code"],
                    "result_id": result["result_id"],
                    "input_hash": result["input_hash"],
                }
            ],
        },
    )
    return {
        "format": "rolling-analytics-certification-v1",
        "policy_hash": result["policy_hash"],
        "manifest": m.model_dump(mode="json"),
        "execution": {"status": {"completionTime": "2025-01-01T00:00:00Z"}},
        "certificate": value,
    }


def test_frozen_calendar_profile_and_additive_rolling_contract():
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    assert len(create_calendar_app(dsn).openapi()["paths"]) == 8
    assert len(create_rolling_app(dsn).openapi()["paths"]) == 9


def test_historical_series_never_claim_current_or_clear_null_quality():
    snapshot = sample(series())
    snapshot["context"]["nav_quality"] = {"signals": [], "methodology_version": "1"}
    result = rolling.derive(snapshot, rolling.policy())
    cert = {
        "certificate_id": "a" * 64,
        "runtime_git_sha": "b" * 40,
        "certified_at": datetime(2024, 2, 1, tzinfo=timezone.utc),
    }
    live = deepcopy(snapshot)
    live["dataset_version"] = 2
    received = store.present(live, snapshot, result, cert)
    assert received["state"] == "historical_certified" and received["historical_window_reusable"]
    assert not received["current_comparison_eligible"]
    live["context"]["nav_quality"]["signals"] = None
    blocked = store.present(live, snapshot, result, cert)
    assert blocked["state"] == "historical_invalidated" and blocked["artifact"]["rolling"] is None


@pytest.mark.postgres
def test_install_replay_acl_defaults_and_prior_calendar_preserved(rolling_db):
    generate(rolling_db)
    with connection(rolling_db, readonly=True) as conn:
        before = source_state(conn)
        prior = capture_review(conn)
        cal = capture_calendar(conn)
    assert installed(rolling_db)["status"] == "INSTALLED"
    assert installed(rolling_db)["status"] == "ALREADY_INSTALLED"
    with connection(rolling_db, readonly=True) as conn:
        assert before == source_state(conn) and cal == capture_calendar(conn)
        after = capture_review(conn)
        for table in ("policies", "snapshots", "results", "events", "certifications"):
            assert all(after.get(table, {}).get(k) == v for k, v in prior.get(table, {}).items())
        for table in operations.TABLES:
            assert not conn.execute(
                "SELECT has_table_privilege('mfdata_ingest',%s,'INSERT,UPDATE,DELETE,TRUNCATE') AS allowed",
                ("mf_rolling." + table,),
            ).fetchone()["allowed"]
    for role in ROLES:
        with connection(rolling_db, role, readonly=True) as conn:
            store.verify_acl(conn)


@pytest.mark.postgres
def test_generation_is_atomic_exact_and_does_not_replace_core_or_calendar(rolling_db):
    core = generate(rolling_db)
    installed(rolling_db)
    with connection(rolling_db, readonly=True) as conn:
        before = source_state(conn)
        cal = capture_calendar(conn)
        snapshot = review.capture(conn, 1)
    with connection(rolling_db, ROLES[0]) as conn:
        with pytest.raises(review.NotFound):
            store.publish(conn, [snapshot, {**snapshot, "code": 999}])
    with connection(rolling_db, readonly=True) as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM mf_rolling.rolling_results").fetchone()["n"]
            == 0
        )
    value = generated(rolling_db)
    assert generated(rolling_db) == value
    with connection(rolling_db, readonly=True) as conn:
        assert source_state(conn) == before and capture_calendar(conn) == cal
        assert review.get_result(conn, 1)["result"]["result_id"] == core["result_id"]
        assert store.get_rolling(conn, 1)["state"] == "not_certified"
    with connection(rolling_db, ROLES[0]) as conn:
        with pytest.raises(ValueError, match="direct migration owner"):
            store.register(conn, certificate(value))


@pytest.mark.postgres
def test_private_registered_pages_admin_audit_and_cursor_consistency(rolling_db):
    installed(rolling_db)
    value = generated(rolling_db)
    with connection(rolling_db) as conn:
        conn.execute((ROOT / "sql/optional/024_research_publication.sql").read_text())
        from mfdataindia.review import publication

        document = publication.policy()
        review._insert_exact(
            conn,
            "policies",
            "policy_hash",
            fingerprint(document),
            document,
            {"policy_id": document["id"], "policy_version": 1},
        )
        store.register(conn, certificate(value))
    key, verifier = trust()
    url = "/api/research/v1/funds/1/rolling-returns"
    headers = {"Authorization": "Bearer " + assertion(key, scope=["review:read"])}
    with TestClient(
        create_rolling_app(runtime_dsn(rolling_db, ROLES[2]), verifier=verifier)
    ) as client:
        assert client.get(url).status_code == 401
        response = client.get(url, params={"limit": 2}, headers=headers)
        assert response.status_code == 200, response.text
        wire = schemas.PagedResponse.model_validate(response.json())
        assert wire.state == "current_artifact_certified" and wire.current_comparison_eligible
        assert len(wire.windows) == 2 and wire.pagination.next_cursor
        assert response.headers["Cache-Control"] == "no-store"
        assert (
            client.get(url, params={"result_id": value["result_id"]}, headers=headers).status_code
            == 403
        )
        audited = client.get(
            url,
            params={"result_id": value["result_id"]},
            headers={"Authorization": "Bearer " + assertion(key)},
        )
        assert audited.status_code == 200, audited.text
        assert audited.json()["result"] == schemas.ResultDocument.model_validate(value).model_dump(
            mode="json"
        )
        cursor = wire.pagination.next_cursor
        second = client.get(url, params={"limit": 2, "cursor": cursor}, headers=headers)
        assert (
            second.status_code == 200
            and second.json()["windows"][0]["end_boundary"] > wire.windows[-1].end_boundary
        )
        assert (
            client.get(url, params={"years": 3, "cursor": cursor}, headers=headers).status_code
            == 422
        )
        assert client.get(url, params={"limit": 101}, headers=headers).status_code == 422
        assert client.get(url, params={"years": 2}, headers=headers).status_code == 422


@pytest.mark.postgres
def test_sealed_lineage_rewrites_and_changed_source_fail_closed(rolling_db):
    installed(rolling_db)
    value = generated(rolling_db)
    with connection(rolling_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM mf_rolling.rolling_results")
    with connection(rolling_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            tampered = deepcopy(value)
            tampered["code"] = 2
            conn.execute(
                "INSERT INTO mf_rolling.rolling_results(result_id,input_hash,policy_hash,engine_fingerprint,document) VALUES(%s,%s,%s,%s,%s)",
                (
                    value["result_id"],
                    value["input_hash"],
                    value["policy_hash"],
                    value["engine_fingerprint"],
                    Jsonb(tampered),
                ),
            )
    with connection(rolling_db, readonly=True) as conn:
        snapshot = review.capture(conn, 1)
    with connection(rolling_db) as conn:
        conn.execute(
            "UPDATE mf.nav_history SET nav=111 WHERE amfi_scheme_code=1 AND nav_date='2024-01-02'"
        )
    with connection(rolling_db, ROLES[0]) as conn:
        with pytest.raises(review.Conflict):
            store.publish(conn, [snapshot])
