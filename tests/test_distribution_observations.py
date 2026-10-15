"""Synthetic tests are not source completeness or live DEV certification."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest
from fastapi.testclient import TestClient
from mfdataindia.enrichment import distributions as engine, distribution_schemas as schemas
from mfdataindia.enrichment import (
    distribution_store as store,
    distribution_operations as operations,
)
from mfdataindia.enrichment import (
    distribution_deployment as deployment,
    idcw_discovery as discovery,
)
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.app import create_overlap_app, create_distribution_observations_app
from test_idcw_discovery import request, pages, IDENTITY
from test_weighted_overlap import prepare as prepare_overlap
from test_official_enrichment_store import verified_db as verified_db
from test_verified_review_store import connection, ROLES, source_state
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import trust, assertion

ROOT = Path(__file__).resolve().parents[1]


def inputs(monkeypatch, *, suffix="", **changes):
    raw = {
        role: b"%PDF-synthetic-only-" + role.encode() + suffix.encode()
        for role in ("history", "notice", "registry")
    }
    body = request(**changes).model_dump(mode="json")
    for role, content in raw.items():
        body[role]["content_hash"] = sha256(content).hexdigest()
    fixture = pages()
    monkeypatch.setattr(
        discovery,
        "decode",
        lambda content, selected: fixture[
            next(role for role, value in raw.items() if value == content)
        ],
    )
    return discovery.IdcwDiscoveryRequest.model_validate(body), raw, fixture


def test_reported_not_complete_replay_unknowns_and_notice_separation(monkeypatch):
    selected, raw, _ = inputs(monkeypatch)
    first = engine.derive(selected, raw)
    assert first == engine.derive(selected, raw)
    assert schemas.DistributionSnapshot.model_validate(first).model_dump(mode="json") == first
    assert len(first["events"]) == 1 and first["events"][0]["amount_inr_per_unit"] == "7.0000"
    assert first["events"][0]["cum_idcw_nav_inr"] == "79.8080"
    assert first["conditional_announcement"]["announced_maximum_inr_per_unit"] == "7.00"
    for key in (
        "complete_event_window_proven",
        "analytics_eligible",
        "freshness_certified",
        "universe_complete",
        "source_data_replaced",
    ):
        assert first[key] is False
    assert first["coverage_state"] == "unknown"
    assert first["events"][0]["reinvestment_nav_inr"] is None
    altered = deepcopy(first)
    altered["analytics_eligible"] = True
    with pytest.raises(ValueError):
        schemas.DistributionSnapshot.model_validate(altered)


def test_empty_window_never_means_no_payout(monkeypatch):
    selected, raw, _ = inputs(monkeypatch, proposed_start="2026-04-01", proposed_end="2026-04-30")
    value = engine.derive(selected, raw)
    assert value["events"] == [] and value["conditional_announcement"] is None
    assert value["coverage_state"] == "unknown" and not value["analytics_eligible"]


def test_conflicts_retained_numeric_tokens_and_cross_snapshot_notice_ceiling(monkeypatch):
    selected, raw, fixture = inputs(monkeypatch)
    a = engine.derive(selected, raw)
    b = deepcopy(a)
    b["events"][0]["amount_inr_per_unit"] = "7.00"
    assert engine.conflicts([a, b]) == []
    b["events"][0]["amount_inr_per_unit"] = "8.00"
    assert engine.conflicts([a, b]) == ["2026-03-05"]
    b = deepcopy(a)
    b["conditional_announcement"]["announced_maximum_inr_per_unit"] = "6.00"
    assert engine.conflicts([a, b]) == ["2026-03-05"]
    fixture["notice"][0]["text"] = fixture["notice"][0]["text"].replace(
        "7.00 81.663", "6.00 81.663"
    )
    assert engine.conflicts([engine.derive(selected, raw)]) == ["2026-03-05"]


def test_new_policy_does_not_mutate_discovery_and_long_window_rejected(monkeypatch):
    selected, raw, _ = inputs(monkeypatch, proposed_start="2024-01-01")
    with pytest.raises(ValueError, match="bounded"):
        engine.derive(selected, raw)
    assert discovery.policy()["publication"] == "discovery_only_not_accepted_events"
    changed = engine.policy()
    changed["bounds"].clear()
    assert engine.policy()["bounds"]


def test_frozen_26_route_contract_and_dormant_29_route_profile(monkeypatch):
    monkeypatch.delenv("MFDATAINDIA_REVIEW_TRUST_FILE", raising=False)
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    old, new = (
        create_overlap_app(dsn).openapi(),
        create_distribution_observations_app(dsn).openapi(),
    )
    assert len(new["paths"]) == 29
    for key, value in old["paths"].items():
        assert new["paths"][key] == value
    for key, value in old["components"]["schemas"].items():
        assert new["components"]["schemas"][key] == value
    with TestClient(create_distribution_observations_app(dsn)) as client:
        assert client.get("/api/distributions/v1/policy").status_code == 503
        assert client.post("/api/distributions/v1/policy", json={}).status_code == 405


def test_exact_bundle_cannot_expand_rollout_objects_identity_or_hash(monkeypatch):
    selected, _, _ = inputs(monkeypatch)
    body = {
        "request": selected.model_dump(mode="json"),
        "sources": {
            role: {
                "intake": getattr(selected, role).model_dump(mode="json"),
                "object_name": f"official-distribution-rollouts/fixture/{role}.pdf",
                "generation": "1",
                "byte_length": 100,
            }
            for role in ("history", "notice", "registry")
        },
    }
    batch = deployment.DistributionBatch.model_validate(body)
    manifest = SimpleNamespace(cohort=[101763], rollout_id="fixture")
    env = {
        "DISTRIBUTION_BATCH": batch.model_dump_json(),
        "DISTRIBUTION_BATCH_HASH": batch.bind(manifest),
    }
    assert deployment.selection(manifest, env) == batch
    for fault in ("object", "missing", "hash", "cohort"):
        revised, m, e = deepcopy(body), manifest, dict(env)
        if fault == "object":
            revised["sources"]["history"]["object_name"] = "outside/fixture.pdf"
        elif fault == "missing":
            del revised["sources"]["registry"]
        elif fault == "hash":
            e["DISTRIBUTION_BATCH_HASH"] = "0" * 64
        else:
            m = SimpleNamespace(cohort=[101762], rollout_id="fixture")
        import json

        e["DISTRIBUTION_BATCH"] = json.dumps(revised)
        with pytest.raises(ValueError):
            deployment.selection(m, e)


def prepare(dsn, monkeypatch):
    prepare_overlap(dsn, monkeypatch)
    with connection(dsn) as conn:
        conn.execute(
            "INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES('HDFC Mutual Fund','HDFC MUTUAL FUND')"
        )
        amc = conn.execute(
            "SELECT amc_id FROM mf.amcs WHERE amfi_amc_name='HDFC Mutual Fund'"
        ).fetchone()["amc_id"]
        conn.execute(
            "INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout,isin_div_reinvest) VALUES(101763,%s,%s,'HDFC FLEXI CAP FUND','OPEN_ENDED','Equity','REGULAR','IDCW','INF179K01582','INF179K01590')",
            (amc, IDENTITY["scheme_name"]),
        )
    with connection(dsn) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        operations.install(conn, ROOT)
    return inputs(monkeypatch)


def append(dsn, selected, raw):
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        return store.append(conn, selected, raw)


@pytest.mark.postgres
def test_pg_default_grants_replay_preservation_and_all_direct_roles(verified_db, monkeypatch):
    selected, raw, _ = prepare(verified_db, monkeypatch)
    with connection(verified_db, readonly=True) as conn:
        before, layers = source_state(conn), operations.prior_layers(conn)
    first = append(verified_db, selected, raw)
    assert append(verified_db, selected, raw) == first
    with connection(verified_db) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        assert operations.install(conn, ROOT)["status"] == "ALREADY_INSTALLED"
        assert source_state(conn) == before and operations.prior_layers(conn) == layers
        assert store.capture(conn)["sources"]["rows"] == 3
        assert store.capture(conn)["snapshots"]["rows"] == 1
        for table in ("sources", "snapshots"):
            assert not conn.execute(
                "SELECT has_table_privilege('mfdata_ingest',%s,'SELECT,INSERT,UPDATE,DELETE') AS yes",
                ("mf_distributions." + table,),
            ).fetchone()["yes"]
    for role in ROLES:
        with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            store.verify_acl(conn)
            assert (
                schemas.DistributionCoverage.model_validate(store.coverage(conn, 101763)).state
                == "observed"
            )
            assert store.get(conn, 101763, first["snapshot_id"])["snapshot"] == first
            assert store.coverage(conn, 2)["observed_record_dates"] is None
            if role != ROLES[0]:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute("SELECT content FROM mf_distributions.sources")


@pytest.mark.postgres
def test_pg_changed_identity_withheld_and_bad_input_atomic(verified_db, monkeypatch):
    selected, raw, _ = prepare(verified_db, monkeypatch)
    first = append(verified_db, selected, raw)
    with pytest.raises(ValueError):
        append(verified_db, selected, {**raw, "history": b"wrong"})
    with connection(verified_db) as conn:
        conn.execute("UPDATE mf.funds SET scheme_name='Changed' WHERE amfi_scheme_code=101763")
    with pytest.raises(review.Conflict):
        append(verified_db, selected, raw)
    with connection(verified_db, readonly=True) as conn:
        value = store.get(conn, 101763, first["snapshot_id"])
        assert value["state"] == "identity_changed" and value["snapshot"] is None
        assert store.capture(conn)["snapshots"]["rows"] == 1


@pytest.mark.postgres
@pytest.mark.parametrize(
    "field",
    [
        "analytics_eligible",
        "complete_event_window_proven",
        "reinvestment_nav",
        "levy_zero",
        "source_link",
        "bad_hash",
    ],
)
def test_pg_direct_insert_cannot_unlock_or_forge_lineage(verified_db, monkeypatch, field):
    selected, raw, _ = prepare(verified_db, monkeypatch)
    value = append(verified_db, selected, raw)
    doc = deepcopy(value)
    if field in ("analytics_eligible", "complete_event_window_proven"):
        doc[field] = True
    elif field == "reinvestment_nav":
        doc["events"][0]["reinvestment_nav_inr"] = "72.8080"
    elif field == "levy_zero":
        doc["events"][0]["scheme_levy_inr_per_unit"] = "0"
    elif field == "source_link":
        doc["source_lineage"]["history"]["content_hash"] = "0" * 64
    doc["snapshot_id"] = fingerprint({k: v for k, v in doc.items() if k != "snapshot_id"})
    if field == "bad_hash":
        doc["snapshot_id"] = "0" * 64
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(
                "INSERT INTO mf_distributions.snapshots SELECT %s,amfi_scheme_code,history_source_id,notice_source_id,registry_source_id,policy_hash,%s FROM mf_distributions.snapshots WHERE snapshot_id=%s",
                (doc["snapshot_id"], Jsonb(doc), value["snapshot_id"]),
            )


@pytest.mark.postgres
def test_pg_conflicts_retained_instead_of_latest_wins_and_no_mutations(verified_db, monkeypatch):
    selected, raw, _ = prepare(verified_db, monkeypatch)
    first = append(verified_db, selected, raw)
    selected, raw, fixture = inputs(monkeypatch, suffix="revised")
    fixture["history"][1]["text"] = fixture["history"][1]["text"].replace(
        "79.8080 7.0000 7.0000", "79.8080 6.0000 6.0000"
    )
    second = append(verified_db, selected, raw)
    assert first["snapshot_id"] != second["snapshot_id"]
    with connection(verified_db, readonly=True) as conn:
        assert store.coverage(conn, 101763)["state"] == "conflicting_evidence"
        assert store.get(conn, 101763, first["snapshot_id"])["snapshot"] == first
    for table in ("sources", "snapshots"):
        with connection(verified_db) as conn:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                conn.execute("DELETE FROM mf_distributions." + table)


@pytest.mark.postgres
def test_pg_private_http_and_no_new_writer(verified_db, monkeypatch):
    from test_historical_store_api import foundations, install as install_historical

    foundations(verified_db)
    install_historical(verified_db)
    selected, raw, _ = prepare(verified_db, monkeypatch)
    snapshot = append(verified_db, selected, raw)
    key, verifier = trust()
    headers = {"Authorization": "Bearer " + assertion(key, scope=["review:read"])}
    with TestClient(
        create_distribution_observations_app(runtime_dsn(verified_db, ROLES[1]), verifier=verifier)
    ) as client:
        paths = [
            "/api/distributions/v1/policy",
            "/api/distributions/v1/funds/101763/coverage",
            "/api/distributions/v1/funds/101763/observations?snapshot_id="
            + snapshot["snapshot_id"],
        ]
        for path in paths:
            assert client.get(path).status_code == 401
            response = client.get(path, headers=headers)
            assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        assert client.get(paths[-1].split("?")[0], headers=headers).status_code == 422
        assert client.post(paths[0], headers=headers, json={}).status_code == 405
        assert client.get("/api/distributions/v1/sources/raw", headers=headers).status_code == 404


@pytest.mark.postgres
def test_pg_failed_first_append_leaves_no_partial_sources_and_acl_drift_rejected(
    verified_db, monkeypatch
):
    selected, raw, _ = prepare(verified_db, monkeypatch)
    with connection(verified_db) as conn:
        conn.execute(
            "UPDATE mf.funds SET scheme_name='Synthetic identity drift' WHERE amfi_scheme_code=101763"
        )
    with pytest.raises(review.Conflict):
        append(verified_db, selected, raw)
    with connection(verified_db, readonly=True) as conn:
        capture = store.capture(conn)
        assert capture["sources"]["rows"] == capture["snapshots"]["rows"] == 0
    with connection(verified_db) as conn:
        conn.execute("GRANT SELECT(content) ON mf_distributions.sources TO mfdata_verified_reader")
    with connection(verified_db, readonly=True) as conn:
        with pytest.raises(ValueError, match="grants"):
            store.verify_acl(conn, principal=ROLES[1])


@pytest.mark.postgres
def test_pg_certification_rejects_missing_or_invented_reference_inputs(verified_db, monkeypatch):
    from mfdataindia.enrichment.distribution_certification import projections, oracle_projection

    selected, raw, _ = prepare(verified_db, monkeypatch)
    document = append(verified_db, selected, raw)
    batch = SimpleNamespace(request=selected)
    oracle = oracle_projection(document)
    with connection(verified_db, readonly=True) as conn:
        assert len(projections(conn, batch, document["snapshot_id"], oracle)) == 3
        missing = deepcopy(oracle)
        del missing["events"][0]["scheme_levy_inr_per_unit"]
        invented = deepcopy(oracle)
        invented["events"][0]["reinvestment_nav_inr"] = "72.8080"
        for bad in (missing, invented):
            with pytest.raises(ValueError, match="oracle"):
                projections(conn, batch, document["snapshot_id"], bad)


@pytest.mark.postgres
def test_pg_snapshot_capacity_is_enforced_but_replay_still_works(verified_db, monkeypatch):
    selected, raw, _ = prepare(verified_db, monkeypatch)
    original = append(verified_db, selected, raw)
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        for index in range(99):
            document = deepcopy(original)
            document["engine_code_hash"] = fingerprint({"synthetic-engine-case": index})
            document["snapshot_id"] = fingerprint(
                {k: v for k, v in document.items() if k != "snapshot_id"}
            )
            conn.execute(
                "INSERT INTO mf_distributions.snapshots SELECT %s,amfi_scheme_code,history_source_id,notice_source_id,registry_source_id,policy_hash,%s FROM mf_distributions.snapshots WHERE snapshot_id=%s",
                (document["snapshot_id"], Jsonb(document), original["snapshot_id"]),
            )
    assert append(verified_db, selected, raw) == original
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        document["engine_code_hash"] = "f" * 64
        document["snapshot_id"] = fingerprint(
            {k: v for k, v in document.items() if k != "snapshot_id"}
        )
        with pytest.raises(psycopg.errors.RaiseException, match="capacity"):
            conn.execute(
                "INSERT INTO mf_distributions.snapshots SELECT %s,amfi_scheme_code,history_source_id,notice_source_id,registry_source_id,policy_hash,%s FROM mf_distributions.snapshots WHERE snapshot_id=%s",
                (document["snapshot_id"], Jsonb(document), original["snapshot_id"]),
            )
