from contextlib import contextmanager

from fastapi.testclient import TestClient

from mfdataindia.calculators import forward
from mfdataindia.review.app import create_calculator_app, create_review_app
from test_verified_review_auth import assertion, trust


def test_additive_profile_keeps_existing_nine_route_factory_unchanged():
    from mfdataindia.review.app import create_rolling_app

    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    assert len(create_rolling_app(dsn).openapi()["paths"]) == 9
    assert len(create_calculator_app(dsn).openapi()["paths"]) == 10


def test_private_calculator_request_is_read_only_no_store_and_no_personal_fields(monkeypatch):
    from mfdataindia.review import app as module
    from mfdataindia.calculators import store

    sql = []

    class Connection:
        @contextmanager
        def transaction(self):
            yield

        def execute(self, text):
            sql.append(text)

    class Pool:
        def __init__(self, **kwargs):
            pass

        def open(self):
            pass

        def close(self):
            pass

        def wait(self, **kwargs):
            pass

        @contextmanager
        def connection(self):
            yield Connection()

    monkeypatch.setattr(module, "ConnectionPool", Pool)
    monkeypatch.setattr(module, "verify_runtime", lambda *args: None)
    monkeypatch.setattr(store, "installed_policy", lambda conn: forward.policy())
    key, verifier = trust()
    app = create_review_app(
        "postgresql://fixture:fixture@127.0.0.1:1/fixture", verifier=verifier, calculators=True
    )
    path = "/api/calculators/v1/projections"
    request = {
        "kind": "sip",
        "months": 12,
        "annual_return_pct": "0",
        "rate_mode": "effective_annual",
        "monthly_paise": 10000,
        "timing": "end",
    }
    headers = {"Authorization": "Bearer " + assertion(key, scope=["review:read"])}
    with TestClient(app) as client:
        assert client.post(path, json=request).status_code == 401
        reply = client.post(path, json=request, headers=headers)
        assert reply.status_code == 200, reply.text
        assert reply.headers["Cache-Control"] == "no-store"
        assert reply.json()["summary"]["closing_paise"] == 120000
        assert reply.json()["personal_scenario_persisted"] is False
        assert reply.json() == client.post(path, json=request, headers=headers).json()
        assert (
            client.post(
                path, json={**request, "customer_name": "not accepted"}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post(path, json={**request, "monthly_paise": True}, headers=headers).status_code
            == 422
        )
        impossible = {
            "kind": "goal",
            "months": 1,
            "annual_return_pct": "0",
            "rate_mode": "effective_annual",
            "cost_today_paise": 100000000000000,
            "annual_inflation_pct": "50",
            "timing": "end",
        }
        failed = client.post(path, json=impossible, headers=headers)
        assert (
            failed.status_code == 422
            and failed.json()["detail"]["reason"] == "goal_infeasible_with_policy_bounds"
        )
    assert sql and all(command.startswith("SET ") for command in sql)
    assert any("READ ONLY" in command for command in sql)
