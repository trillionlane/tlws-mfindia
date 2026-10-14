from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from mfdataindia.calculators import historical, historical_store, store as forward_store
from mfdataindia.review import store as review, publication
from mfdataindia.review.app import create_historical_calculator_app, create_calculator_app
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.publication_operations import capture_review
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import assertion, trust
from test_verified_review_store import ROLES, connection, source_state
from test_verified_review_store import verified_db as _verified_db

verified_db = _verified_db
ROOT = Path(__file__).resolve().parents[1]


def install(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        return historical_store.install_policy(conn)


def foundations(dsn):
    from mfdataindia.analytics.operations import install as calendar_install
    from mfdataindia.analytics.rolling_operations import install as rolling_install

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        calendar_install(conn, ROOT)
        rolling_install(conn, ROOT)
    with connection(dsn) as conn:
        conn.execute((ROOT / "sql/optional/024_research_publication.sql").read_text())
        doc = publication.policy()
        review._insert_exact(
            conn,
            "policies",
            "policy_hash",
            fingerprint(doc),
            doc,
            {"policy_id": doc["id"], "policy_version": 1},
        )
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        forward_store.install_policy(conn)


def test_eleven_route_profile_is_additive_without_changing_the_ten_route_factory():
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    assert len(create_calculator_app(dsn).openapi()["paths"]) == 10
    assert len(create_historical_calculator_app(dsn).openapi()["paths"]) == 11


@pytest.mark.postgres
def test_policy_install_replay_preserves_source_prior_rows_and_sequence(verified_db):
    with connection(verified_db, readonly=True) as conn:
        before, prior = source_state(conn), capture_review(conn)
    first = install(verified_db)
    assert install(verified_db) == first
    with connection(verified_db, readonly=True) as conn:
        assert source_state(conn) == before
        latest = capture_review(conn)
        assert set(latest["policies"]) - set(prior["policies"]) == {first["policy_hash"]}
        for table in prior:
            if table != "policies":
                assert latest[table] == prior[table]
    for role in ROLES:
        with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            assert historical_store.installed_policy(conn) == historical.policy()
            with pytest.raises(ValueError, match="direct migration owner"):
                historical_store.install_policy(conn)


@pytest.mark.postgres
def test_private_simulations_use_real_read_only_snapshot_and_append_no_personal_data(verified_db):
    foundations(verified_db)
    install(verified_db)
    with connection(verified_db, readonly=True) as conn:
        before, prior = source_state(conn), capture_review(conn)
        snapshot = review.capture(conn, 1)
    body = {
        "kind": "sip",
        "code": 1,
        "start_date": snapshot["observations"][0]["date"],
        "end_date": snapshot["observations"][-1]["date"],
        "monthly_paise": 100000,
        "day_of_month": 15,
    }
    key, verifier = trust()
    headers = {"Authorization": "Bearer " + assertion(key, scope=["review:read"])}
    with TestClient(
        create_historical_calculator_app(runtime_dsn(verified_db, ROLES[2]), verifier=verifier)
    ) as client:
        path = "/api/calculators/v1/backtests"
        assert client.post(path, json=body).status_code == 401
        expected = historical.derive(body, snapshot, historical.policy())
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == expected
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json() == client.post(path, json=body, headers=headers).json()
        assert response.json()["available"] is True
        assert response.json()["summary"]["xirr_pct"] is not None
        for invalid in (
            {**body, "monthly_paise": True},
            {**body, "customer_name": "rejected"},
            {**body, "start_date": "2023-02-30"},
        ):
            assert client.post(path, json=invalid, headers=headers).status_code == 422
        assert (
            client.post(path, json={**body, "code": 2}, headers=headers).json()[
                "unavailable_reason"
            ]
            == "unsupported_option"
        )
        assert client.post(path, json={**body, "code": 999}, headers=headers).status_code == 404
    with connection(verified_db, readonly=True) as conn:
        assert source_state(conn) == before and capture_review(conn) == prior
