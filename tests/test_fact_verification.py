"""Synthetic/clone review mechanics; never evidence of live human verification."""

from pathlib import Path
import psycopg
import pytest
from psycopg.rows import dict_row
from mfdataindia.enrichment import fact_review_store as reviews, fact_review_schemas
from mfdataindia.enrichment import current_facts_store as current, monthly_projection, refresh_store
from mfdataindia.review.app import create_fact_refresh_app, create_fact_verification_app
from test_official_fact_refresh import setup, append_period, receipt, references
from test_verified_review_store import connection, source_state, ROLES
from test_verified_review_store import verified_db as verified_db
from test_verified_review_api import runtime_dsn

ROOT = Path(__file__).resolve().parents[1]


def prepared(dsn, monkeypatch):
    selection, _, before = setup(dsn, monkeypatch)
    snapshots = append_period(dsn, monkeypatch, selection)
    with connection(dsn) as conn:
        refresh_store.append_run(conn, receipt(snapshots, "review-bootstrap"))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        monthly_projection.activate(
            conn, ROOT, legacy_snapshot_ids=[], reviewed=references(snapshots)
        )
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        assert reviews.install(conn, ROOT)["status"] == "INSTALLED"
    return selection, snapshots, before


def request(state, snapshot_id, *, fields=None, decision="verified"):
    return fact_review_schemas.FactReviewMutation(
        expected_version=state["version"],
        expected_dependency_hash=state["dependency_hash"],
        decision=decision,
        attestation="human_reviewed_original",
        fields=fields or ["risk_level"],
        evidence_snapshot_ids=[snapshot_id],
        reason="Synthetic clone test only; no live human verification",
    ).model_dump(mode="json")


def test_verification_contract_is_additive_and_requires_explicit_human_attestation():
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    old, new = create_fact_refresh_app(dsn).openapi(), create_fact_verification_app(dsn).openapi()
    assert len(new["paths"]) == 43
    assert all(new["paths"][k] == v for k, v in old["paths"].items())
    assert all(
        new["components"]["schemas"][k] == v for k, v in old["components"]["schemas"].items()
    )
    with pytest.raises(ValueError):
        fact_review_schemas.FactReviewMutation.model_validate(
            {"expected_version": 0, "decision": "verified"}
        )


@pytest.mark.postgres
def test_owner_current_projection_checks_survive_verification_installation(
    verified_db, monkeypatch
):
    _, snapshots, _ = prepared(verified_db, monkeypatch)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert (
            current.get(conn, 103174)["fields"]["risk_level"]["verification_status"]
            == "needs_verification"
        )
        result = monthly_projection.activate(
            conn, ROOT, legacy_snapshot_ids=[], reviewed=references(snapshots)
        )
        assert result["status"] == "ALREADY_ACTIVE"
        assert reviews.capture(conn)["events"]["rows"] == 0


@pytest.mark.postgres
def test_signed_private_admin_http_and_read_scope_cannot_verify(verified_db, monkeypatch):
    from fastapi.testclient import TestClient
    from test_verified_review_auth import trust, assertion
    from mfdataindia.review.app import create_review_app

    _, snapshots, _ = prepared(verified_db, monkeypatch)
    key, verifier = trust()
    # This synthetic fixture installs only the facts stack. The full 43-route
    # profile is separately checked against the actual restored DEV schema.
    app = create_review_app(
        runtime_dsn(verified_db, ROLES[2]),
        verifier=verifier,
        current_facts=True,
        monthly_facts=True,
        fact_refresh=True,
        fact_verification=True,
    )
    admin = {
        "Authorization": "Bearer " + assertion(key, sub="clone-human-test"),
        "Idempotency-Key": "clone-http-review-0001",
    }
    reader = {"Authorization": "Bearer " + assertion(key, scope=["review:read"])}
    base = "/api/enrichment/v6/funds/103174"
    sid = next(s["snapshot_id"] for s in snapshots if s["code"] == 103174)
    with TestClient(app) as client:
        denied = client.get(base + "/verification", headers={"X-Actor": "fake-admin"})
        assert denied.status_code == 401 and denied.headers["Cache-Control"] == "no-store"
        state = client.get(base + "/verification", headers=reader)
        assert state.status_code == 200
        body = request(state.json(), sid)
        assert (
            client.post(
                base + "/reviews",
                json=body,
                headers={**reader, "Idempotency-Key": "clone-read-0001"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                base + "/reviews", json={**body, "actor": "forged"}, headers=admin
            ).status_code
            == 422
        )
        reply = client.post(base + "/reviews", json=body, headers=admin)
        assert reply.status_code == 200 and reply.headers["Cache-Control"] == "no-store"
        assert reply.json()["actor"] == "clone-human-test" and reply.json()["caller"] == "insights"
        assert client.post(base + "/reviews", json=body, headers=admin).json() == reply.json()
        assert (
            client.get("/api/enrichment/v3/funds/103174/facts", headers=reader).json()["fields"][
                "risk_level"
            ]["verification_status"]
            == "verified"
        )
        assert (
            client.post(
                base + "/reviews",
                json=body,
                headers={**admin, "Idempotency-Key": "clone-http-review-0002"},
            ).status_code
            == 409
        )


@pytest.mark.postgres
def test_evidence_bound_review_replay_and_new_inputs_require_revalidation(verified_db, monkeypatch):
    selected, snapshots, before = prepared(verified_db, monkeypatch)
    reader = runtime_dsn(verified_db, ROLES[1])
    admin = runtime_dsn(verified_db, ROLES[2])
    snapshot = next(s for s in snapshots if s["code"] == 103174)
    with psycopg.connect(reader, row_factory=dict_row) as conn:
        initial = reviews.state(conn, 103174)
        revision = current.get(conn, 103174)["facts_revision"]
        assert (
            initial["version"] == 0
            and initial["fields"]["risk_level"]["status"] == "needs_verification"
        )
    body = request(initial, snapshot["snapshot_id"])
    with psycopg.connect(admin, row_factory=dict_row) as conn:
        event = reviews.record(
            conn,
            103174,
            body,
            caller="clone-backend",
            actor="clone-human",
            idempotency_key="clone-review-0001",
        )
    with psycopg.connect(reader, row_factory=dict_row) as conn:
        assert reviews.state(conn, 103174)["fields"]["risk_level"]["status"] == "verified"
        assert (
            current.get(conn, 103174)["fields"]["risk_level"]["verification_status"] == "verified"
        )
        assert current.get(conn, 103174)["facts_revision"] == revision
    for _ in range(2):
        with psycopg.connect(admin, row_factory=dict_row) as conn:
            assert (
                reviews.record(
                    conn,
                    103174,
                    body,
                    caller="clone-backend",
                    actor="clone-human",
                    idempotency_key="clone-review-0001",
                )
                == event
            )
    later = append_period(
        verified_db, monkeypatch, selected, month="October", as_of="2026-10-31", ber="1.25"
    )
    with connection(verified_db) as conn:
        refresh_store.append_run(conn, receipt(later, "review-new-month"))
    with psycopg.connect(reader, row_factory=dict_row) as conn:
        state = reviews.state(conn, 103174)
        assert state["fields"]["risk_level"]["status"] == "needs_revalidation"
        assert (
            current.get(conn, 103174)["fields"]["risk_level"]["verification_status"]
            == "needs_revalidation"
        )
    with psycopg.connect(admin, row_factory=dict_row) as conn:
        assert (
            reviews.record(
                conn,
                103174,
                body,
                caller="clone-backend",
                actor="clone-human",
                idempotency_key="clone-review-0001",
            )
            == event
        )
    with psycopg.connect(admin, row_factory=dict_row) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="source/version changed"):
            reviews.record(
                conn,
                103174,
                body,
                caller="clone-backend",
                actor="clone-human",
                idempotency_key="clone-review-0002",
            )
    # A review of another field must not clear the old risk review's staleness.
    newer = next(s for s in later if s["code"] == 103174)
    with psycopg.connect(admin, row_factory=dict_row) as conn:
        reviews.record(
            conn,
            103174,
            request(state, newer["snapshot_id"], fields=["fund_managers"]),
            caller="clone-backend",
            actor="clone-human",
            idempotency_key="clone-review-0003",
        )
    with psycopg.connect(reader, row_factory=dict_row) as conn:
        state = reviews.state(conn, 103174)
        assert state["fields"]["risk_level"]["status"] == "needs_revalidation"
        assert state["fields"]["fund_managers"]["status"] == "verified"
    with connection(verified_db, readonly=True) as conn:
        assert source_state(conn) == before
        assert reviews.capture(conn)["events"]["rows"] == 2


@pytest.mark.postgres
def test_direct_role_boundaries_and_bad_evidence_fail_closed(verified_db, monkeypatch):
    _, snapshots, _ = prepared(verified_db, monkeypatch)
    with psycopg.connect(runtime_dsn(verified_db, ROLES[1]), row_factory=dict_row) as conn:
        state = reviews.state(conn, 103174)
    sid = next(s["snapshot_id"] for s in snapshots if s["code"] == 103174)
    body = request(state, sid)
    for role in ("mfdata_ingest", *ROLES):
        with connection(verified_db, readonly=True) as conn:
            assert not conn.execute(
                "SELECT has_table_privilege(%s,'mf_fact_review.events','INSERT,UPDATE,DELETE,TRUNCATE') AS allowed",
                (role,),
            ).fetchone()["allowed"]
    with psycopg.connect(runtime_dsn(verified_db, ROLES[1]), row_factory=dict_row) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            reviews.record(
                conn,
                103174,
                body,
                caller="clone-backend",
                actor="clone-human",
                idempotency_key="clone-denied-0001",
            )
    for change in (
        {"evidence_snapshot_ids": ["0" * 64]},
        {
            "fields": ["aum"],
            "evidence_snapshot_ids": [
                next(s["snapshot_id"] for s in snapshots if s["code"] == 103173)
            ],
        },
        {"expected_dependency_hash": "0" * 64},
    ):
        with psycopg.connect(runtime_dsn(verified_db, ROLES[2]), row_factory=dict_row) as conn:
            with pytest.raises(psycopg.errors.RaiseException):
                reviews.record(
                    conn,
                    103174,
                    {**body, **change},
                    caller="clone-backend",
                    actor="clone-human",
                    idempotency_key="clone-denied-0002",
                )


@pytest.mark.postgres
def test_history_change_reads_and_append_only_review_status_do_not_apply_source_edits(
    verified_db, monkeypatch
):
    selected, first, before = prepared(verified_db, monkeypatch)
    later = append_period(
        verified_db, monkeypatch, selected, month="October", as_of="2026-10-31", ber="1.25"
    )
    with connection(verified_db) as conn:
        refresh_store.append_run(conn, receipt(later, "history-later"))
    source = next(s for s in first if s["code"] == 103174)
    target = next(s for s in later if s["code"] == 103174)
    with psycopg.connect(runtime_dsn(verified_db, ROLES[1]), row_factory=dict_row) as conn:
        history = reviews.history(conn, 103174, limit=1)
        assert len(history["snapshots"]) == 1 and history["snapshots"][0]["published"]
        next_page = reviews.history(conn, 103174, after_id=history["next_after_id"], limit=1)
        assert (
            next_page["snapshots"][0]["snapshot"]["snapshot_id"]
            != history["snapshots"][0]["snapshot"]["snapshot_id"]
        )
        changes = reviews.changes(conn, 103174, source["snapshot_id"], target["snapshot_id"])
        assert "base_expense_ratio" in changes["fields"]
        assert "risk_level" not in changes["fields"]
        assert not changes["changes_applied"] and not changes["human_verification_inferred"]
        with pytest.raises(LookupError):
            reviews.changes(conn, 103173, source["snapshot_id"], target["snapshot_id"])
    with connection(verified_db, readonly=True) as conn:
        assert source_state(conn) == before
        assert reviews.capture(conn)["events"]["rows"] == 0


@pytest.mark.postgres
def test_changed_actor_or_request_cannot_reuse_key_and_current_conflicts_cannot_be_verified(
    verified_db, monkeypatch
):
    selected, snapshots, _ = prepared(verified_db, monkeypatch)
    sid = next(s["snapshot_id"] for s in snapshots if s["code"] == 103174)
    admin = runtime_dsn(verified_db, ROLES[2])
    with psycopg.connect(admin, row_factory=dict_row) as conn:
        body = request(reviews.state(conn, 103174), sid)
        reviews.record(
            conn, 103174, body, caller="clone", actor="human-a", idempotency_key="identity-key-0001"
        )
    for actor, modified in [
        ("human-b", body),
        ("human-a", {**body, "reason": "A different meaningful reason"}),
    ]:
        with psycopg.connect(admin, row_factory=dict_row) as conn:
            with pytest.raises(psycopg.errors.RaiseException, match="idempotency"):
                reviews.record(
                    conn,
                    103174,
                    modified,
                    caller="clone",
                    actor=actor,
                    idempotency_key="identity-key-0001",
                )
    for suffix, ber in [("a", "1.25"), ("b", "1.27")]:
        later = append_period(
            verified_db, monkeypatch, selected, month="October", as_of="2026-10-31", ber=ber
        )
        with connection(verified_db) as conn:
            refresh_store.append_run(conn, receipt(later, "conflict-" + suffix))
    with psycopg.connect(admin, row_factory=dict_row) as conn:
        body = request(reviews.state(conn, 103174), sid, fields=["base_expense_ratio"])
        with pytest.raises(psycopg.errors.RaiseException, match="conflicting"):
            reviews.record(
                conn,
                103174,
                body,
                caller="clone",
                actor="human-a",
                idempotency_key="identity-key-0002",
            )


@pytest.mark.postgres
def test_dependency_change_rejects_review_even_when_review_version_is_unchanged(
    verified_db, monkeypatch
):
    selected, first, _ = prepared(verified_db, monkeypatch)
    admin = runtime_dsn(verified_db, ROLES[2])
    with psycopg.connect(admin, row_factory=dict_row) as conn:
        state = reviews.state(conn, 103174)
    body = request(state, next(s["snapshot_id"] for s in first if s["code"] == 103174))
    later = append_period(
        verified_db, monkeypatch, selected, month="October", as_of="2026-10-31", ber="1.25"
    )
    with connection(verified_db) as conn:
        refresh_store.append_run(conn, receipt(later, "dependency-only-change"))
    with psycopg.connect(admin, row_factory=dict_row) as conn:
        fresh = reviews.state(conn, 103174)
        assert fresh["version"] == state["version"] == 0
        assert fresh["dependency_hash"] != state["dependency_hash"]
        with pytest.raises(psycopg.errors.RaiseException, match="source/version changed"):
            reviews.record(
                conn,
                103174,
                body,
                caller="clone",
                actor="human",
                idempotency_key="dependency-key-0001",
            )


@pytest.mark.postgres
def test_competing_review_writers_cannot_commit_two_events_for_one_expected_version(
    verified_db, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    _, snapshots, _ = prepared(verified_db, monkeypatch)
    admin = runtime_dsn(verified_db, ROLES[2])
    with psycopg.connect(admin, row_factory=dict_row) as conn:
        body = request(
            reviews.state(conn, 103174),
            next(s["snapshot_id"] for s in snapshots if s["code"] == 103174),
        )

    def write(index):
        try:
            with psycopg.connect(admin, row_factory=dict_row) as conn:
                return reviews.record(
                    conn,
                    103174,
                    body,
                    caller="clone",
                    actor="human",
                    idempotency_key="concurrent-key-000" + str(index),
                )
        except psycopg.errors.RaiseException:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, [1, 2]))
    assert sum(result is not None for result in outcomes) == 1
    with psycopg.connect(admin, row_factory=dict_row) as conn:
        assert reviews.state(conn, 103174)["version"] == 1
        assert reviews.capture(conn)["events"]["rows"] == 1
