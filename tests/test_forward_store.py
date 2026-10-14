from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from mfdataindia.calculators import forward, store
from mfdataindia.review import store as review
from mfdataindia.review.app import create_calculator_app
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
        return store.install_policy(conn)


@pytest.mark.postgres
def test_only_new_policy_is_persisted_with_no_source_or_prior_row_changes(verified_db):
    with connection(verified_db, readonly=True) as conn:
        before = source_state(conn)
        prior = capture_review(conn)
    first = install(verified_db)
    assert install(verified_db) == first
    assert first["schema_changes"] == 0 and first["personal_scenarios_persisted"] == 0
    with connection(verified_db, readonly=True) as conn:
        assert source_state(conn) == before
        latest = capture_review(conn)
        for table in ("policies", "snapshots", "results", "events", "certifications"):
            assert all(latest.get(table, {}).get(k) == v for k, v in prior.get(table, {}).items())
        assert set(latest["policies"]) - set(prior["policies"]) == {first["policy_hash"]}
        assert latest["snapshots"] == prior["snapshots"] and latest["results"] == prior["results"]
        assert (
            latest["events"] == prior["events"]
            and latest["event_sequence"] == prior["event_sequence"]
        )
    for role in ROLES:
        with connection(verified_db, role, readonly=True) as conn:
            assert store.installed_policy(conn) == forward.policy()


@pytest.mark.postgres
def test_runtime_cannot_execute_owner_installer_or_modify_registered_policy(verified_db):
    install(verified_db)
    for role in ROLES:
        with connection(verified_db, role) as conn:
            with pytest.raises(ValueError, match="direct migration owner"):
                store.install_policy(conn)
        with connection(verified_db, role) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE mf_review.policies SET policy_id=policy_id")


@pytest.mark.postgres
def test_real_private_calculator_app_does_not_persist_scenarios(verified_db):
    from mfdataindia.analytics.operations import install as calendar_install
    from mfdataindia.analytics.rolling_operations import install as rolling_install
    from mfdataindia.review import publication
    from mfdataindia.review.policy import fingerprint

    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        calendar_install(conn, ROOT)
        rolling_install(conn, ROOT)
    with connection(verified_db) as conn:
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
    install(verified_db)
    with connection(verified_db, readonly=True) as conn:
        before = source_state(conn)
        prior = capture_review(conn)
    key, verifier = trust()
    headers = {"Authorization": "Bearer " + assertion(key, scope=["review:read"])}
    request = {
        "kind": "sip",
        "months": 24,
        "annual_return_pct": "10",
        "rate_mode": "effective_annual",
        "monthly_paise": 100000,
        "timing": "end",
    }
    with TestClient(
        create_calculator_app(runtime_dsn(verified_db, ROLES[2]), verifier=verifier)
    ) as client:
        path = "/api/calculators/v1/projections"
        assert client.post(path, json=request).status_code == 401
        actual = client.post(path, json=request, headers=headers)
        assert actual.status_code == 200, actual.text
        assert actual.json() == forward.derive(request, forward.policy())
        assert actual.headers["Cache-Control"] == "no-store"
        assert actual.json() == client.post(path, json=request, headers=headers).json()
    with connection(verified_db, readonly=True) as conn:
        assert before == source_state(conn) and prior == capture_review(conn)
