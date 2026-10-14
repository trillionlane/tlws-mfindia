from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from mfdataindia.analytics import calendar, operations, schemas, store
from mfdataindia.review.app import create_calendar_app, create_research_app, create_review_app
from mfdataindia.review.policy import fingerprint
from mfdataindia.review import store as review
from mfdataindia.review.publication_operations import capture_review
from mfdataindia.review.rollout import seal_evidence
from test_calendar_analytics import sample
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import assertion, trust
from test_verified_review_store import (
    ROLES,
    connection,
    generate,
    source_state,
    verified_db as _verified_db,
)
from test_verified_review_rollout import manifest

verified_db = _verified_db
ROOT = Path(__file__).resolve().parents[1]


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
    m = manifest()
    m = m.model_copy(update={"cohort": [result["code"]]})
    value = seal_evidence(
        m,
        "calendar-http",
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
        "format": "calendar-analytics-certification-v1",
        "policy_hash": result["policy_hash"],
        "manifest": m.model_dump(mode="json"),
        "execution": {"status": {"completionTime": "2025-01-01T00:00:00Z"}},
        "certificate": value,
    }


def test_profile_preserves_existing_contract_paths():
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    assert len(create_review_app(dsn).openapi()["paths"]) == 6
    assert len(create_research_app(dsn).openapi()["paths"]) == 7
    assert len(create_calendar_app(dsn).openapi()["paths"]) == 8


def test_historical_reuse_never_current_or_unassessed_override():
    snapshot = sample([("2023-12-31", "100"), ("2024-01-31", "110")])
    snapshot["context"]["nav_quality"] = {"signals": [], "methodology_version": "1"}
    result = calendar.derive(snapshot, calendar.policy())
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
    assert blocked["state"] == "historical_invalidated" and blocked["artifact"]["calendar"] is None


@pytest.mark.postgres
def test_install_replay_runtime_acl_defaults_and_source_preservation(verified_db):
    generate(verified_db)
    with connection(verified_db, readonly=True) as conn:
        before = source_state(conn)
        prior = capture_review(conn)
    assert installed(verified_db)["status"] == "INSTALLED"
    assert installed(verified_db)["status"] == "ALREADY_INSTALLED"
    with connection(verified_db, readonly=True) as conn:
        assert before == source_state(conn)
        after = capture_review(conn)
        assert all(after["policies"][k] == v for k, v in prior["policies"].items())
        assert after["results"] == prior["results"] and after["snapshots"] == prior["snapshots"]
        for table in operations.TABLES:
            assert not conn.execute(
                "SELECT has_table_privilege(%s,%s,'INSERT,UPDATE,DELETE,TRUNCATE') AS allowed",
                ("mfdata_ingest", "mf_analytics." + table),
            ).fetchone()["allowed"]
    for role in ROLES:
        with connection(verified_db, role, readonly=True) as conn:
            store.verify_acl(conn)


@pytest.mark.postgres
def test_generation_is_atomic_and_source_core_results_retained(verified_db):
    original = generate(verified_db)
    installed(verified_db)
    with connection(verified_db, readonly=True) as conn:
        before = source_state(conn)
        snapshot = review.capture(conn, 1)
    with connection(verified_db, ROLES[0]) as conn:
        with pytest.raises(review.NotFound):
            store.publish(conn, [snapshot, {**snapshot, "code": 999}])
    with connection(verified_db, readonly=True) as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM mf_analytics.calendar_results").fetchone()["n"]
            == 0
        )
    value = generated(verified_db)
    assert generated(verified_db) == value
    with connection(verified_db, readonly=True) as conn:
        assert source_state(conn) == before
        assert review.get_result(conn, 1)["result"]["result_id"] == original["result_id"]
        assert store.get_calendar(conn, 1)["state"] == "not_certified"
    with connection(verified_db, ROLES[0]) as conn:
        with pytest.raises(ValueError, match="direct migration owner"):
            store.register(conn, certificate(value))


@pytest.mark.postgres
def test_private_reads_registration_and_retained_audit_auth(verified_db):
    installed(verified_db)
    value = generated(verified_db)
    with connection(verified_db) as conn:
        # Required base publication profile, separate from calendar storage.
        conn.execute((ROOT / "sql/optional/024_research_publication.sql").read_text())
        from mfdataindia.review import publication

        review._insert_exact(
            conn,
            "policies",
            "policy_hash",
            fingerprint(publication.policy()),
            publication.policy(),
            {"policy_id": publication.policy()["id"], "policy_version": 1},
        )
        store.register(conn, certificate(value))
    private, verifier = trust()
    url = "/api/research/v1/funds/1/calendar-returns"
    headers = {"Authorization": "Bearer " + assertion(private, scope=["review:read"])}
    with TestClient(
        create_calendar_app(runtime_dsn(verified_db, ROLES[2]), verifier=verifier)
    ) as client:
        assert client.get(url).status_code == 401
        response = client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        assert (
            response.json()["state"] == "current_artifact_certified"
            and response.json()["current_comparison_eligible"]
        )
        assert response.headers["Cache-Control"] == "no-store"
        assert (
            client.get(url + "?result_id=" + value["result_id"], headers=headers).status_code == 403
        )
        audited = client.get(
            url + "?result_id=" + value["result_id"],
            headers={"Authorization": "Bearer " + assertion(private)},
        )
        assert audited.status_code == 200, audited.text
        assert audited.json()["audit_only"] and audited.json()[
            "result"
        ] == schemas.ResultDocument.model_validate(value).model_dump(mode="json")


@pytest.mark.postgres
def test_seals_rewrites_and_changed_dependencies_fail_closed(verified_db):
    installed(verified_db)
    value = generated(verified_db)
    with connection(verified_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM mf_analytics.calendar_results")
    with connection(verified_db) as conn:
        tampered = deepcopy(value)
        tampered["code"] = 2
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(
                "INSERT INTO mf_analytics.calendar_results(result_id,input_hash,policy_hash,engine_fingerprint,document) VALUES(%s,%s,%s,%s,%s)",
                (
                    value["result_id"],
                    value["input_hash"],
                    value["policy_hash"],
                    value["engine_fingerprint"],
                    Jsonb(tampered),
                ),
            )
    with connection(verified_db, readonly=True) as conn:
        snapshot = review.capture(conn, 1)
    with connection(verified_db) as conn:
        conn.execute(
            "UPDATE mf.nav_history SET nav=123 WHERE amfi_scheme_code=1 AND nav_date='2024-01-02'"
        )
    with connection(verified_db, ROLES[0]) as conn:
        with pytest.raises(review.Conflict):
            store.publish(conn, [snapshot])


@pytest.mark.postgres
def test_startup_rejects_disabled_sealing_and_extra_owned_table(verified_db):
    installed(verified_db)
    with connection(verified_db) as conn:
        conn.execute(
            "ALTER TABLE mf_analytics.calendar_results DISABLE TRIGGER calendar_result_sealed"
        )
        with pytest.raises(ValueError, match="trigger drift"):
            store.verify_acl(conn, principal=ROLES[1])
        conn.rollback()
    with connection(verified_db) as conn:
        conn.execute("CREATE TABLE mf_analytics.unmodelled(value integer)")
        with pytest.raises(ValueError, match="inventory"):
            store.verify_acl(conn, principal=ROLES[1])
        with pytest.raises(ValueError, match="inventory"):
            operations.capture_calendar(conn)
