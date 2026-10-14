"""No synthetic proof is labelled a DEV certificate."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from conftest import certify_comparison_fixture

from mfdataindia.review import publication, publication_operations, store
from mfdataindia.review.app import create_research_app, create_review_app
from mfdataindia.review.policy import fingerprint, policy
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.rollout import seal_evidence
from mfdataindia.review.rollout_cloud import (
    RESEARCH_FACTORY,
    api_args,
    revision_name,
    verify_revision,
)
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import assertion, trust
from test_verified_review_rollout import manifest, revision
from test_verified_review_store import (
    ROLES,
    connection,
    generate,
    source_state,
    verified_db as _verified_db,
)

verified_db = _verified_db
ROOT = Path(__file__).resolve().parents[1]


def sample():
    value = {
        "code": 1,
        "source_revision": 1,
        "dataset_version": 1,
        "identity": {"option_type": "GROWTH", "last_seen_in_source": "2024-01-02"},
        "facts": None,
        "observations": [
            {"date": "2024-01-01", "nav": "100"},
            {"date": "2024-01-02", "nav": "110"},
        ],
        "context": {
            "nav_quality": {
                "assessment_status": "current",
                "signals": [],
                "methodology_version": "1",
            },
            "methodology": {"comparison_eligible": True},
        },
    }
    return (
        value,
        store.derive(value, policy()),
        {
            "certificate_id": "c" * 64,
            "runtime_git_sha": "a" * 40,
            "certified_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
        },
    )


def test_exact_current_artifact_and_independent_eligibility():
    value, result, cert = sample()
    state = publication.present(value, value, result, cert)
    assert state["state"] == "current_artifact_certified"
    assert state["current_comparison_eligible"] and state["historical_window_reusable"]
    assert state["last_certified"]["metrics"]["cumulative_return_pct"] == "10.00000000"


def test_refresh_cannot_bless_new_inputs_with_old_certificate():
    value, result, cert = sample()
    live = deepcopy(value)
    live["dataset_version"] = 2
    live["context"]["nav_quality"]["assessment_status"] = "stale"
    live["context"]["methodology"]["comparison_eligible"] = False
    state = publication.present(live, value, result, cert)
    assert state["state"] == "historical_certified" and state["historical_window_reusable"]
    assert not state["current_comparison_eligible"]
    assert state["last_certified"]["dataset_version"] == 1 and state["current_dataset_version"] == 2
    assert state["last_certified"]["metrics"] == result["metrics"]


def test_append_and_last_seen_refresh_do_not_relabel_or_destroy_original_window():
    value, result, cert = sample()
    live = deepcopy(value)
    live["source_revision"] = 2
    live["dataset_version"] = 2
    live["identity"]["last_seen_in_source"] = "2024-01-03"
    live["observations"].append({"date": "2024-01-03", "nav": "120"})
    state = publication.present(live, value, result, cert)
    assert state["state"] == "historical_certified" and not state["current_comparison_eligible"]
    assert state["last_certified"]["as_of"] == "2024-01-02"
    assert state["last_certified"]["metrics"]["cumulative_return_pct"] == "10.00000000"


@pytest.mark.parametrize(
    "change,reason",
    [
        ("correction", "historical_observations_changed"),
        ("removed", "historical_observations_changed"),
        ("inserted", "historical_observations_changed"),
        ("identity", "identity_changed"),
        ("signal", "disclosed_quality_signal"),
        ("null", "quality_evidence_unavailable"),
        ("methodology", "quality_methodology_changed"),
    ],
)
def test_invalidating_dependencies_withhold_metrics_but_preserve_artifact(change, reason):
    value, result, cert = sample()
    live = deepcopy(value)
    original = deepcopy(result)
    if change == "correction":
        live["observations"][0]["nav"] = "90"
    elif change == "removed":
        live["observations"].pop(0)
    elif change == "inserted":
        live["observations"].insert(0, {"date": "2023-12-29", "nav": "90"})
    elif change == "identity":
        live["identity"]["option_type"] = "IDCW"
    elif change == "signal":
        live["context"]["nav_quality"]["signals"] = ["duplicate_variant_series"]
    elif change == "null":
        live["context"]["nav_quality"]["signals"] = None
    else:
        live["context"]["nav_quality"]["methodology_version"] = "future"
    state = publication.present(live, value, result, cert)
    assert state["state"] == "historical_invalidated" and reason in state["withheld_reasons"]
    assert not state["current_comparison_eligible"] and not state["historical_window_reusable"]
    assert state["last_certified"]["metrics"] is None and result == original


def test_missing_certificate_never_falls_back_to_generated_or_legacy_metrics():
    value, _, _ = sample()
    state = publication.present(value, None, None, None)
    assert state["last_certified"] is None and state["state"] == "not_certified"
    assert not state["current_comparison_eligible"]


def test_current_inputs_cannot_reactivate_unsupported_policy_or_engine():
    value, result, cert = sample()
    for field in ("policy_hash", "engine_version", "engine_code"):
        changed = deepcopy(result)
        if field == "engine_code":
            changed["engine"]["code_hash"] = "0" * 64
        else:
            changed[field] = "unsupported"
        state = publication.present(value, value, changed, cert)
        assert (
            not state["current_comparison_eligible"] and state["last_certified"]["metrics"] is None
        )
        assert "metric_policy_or_engine_not_supported" in state["withheld_reasons"]


def test_unsupported_option_not_unlocked_by_conformance_certificate():
    value, _, cert = sample()
    value["identity"]["option_type"] = "IDCW"
    result = store.derive(value, policy())
    state = publication.present(value, value, result, cert)
    assert state["state"] == "current_artifact_certified"
    assert not state["current_comparison_eligible"] and state["last_certified"]["metrics"] is None
    assert state["withheld_reasons"] == ["ineligible_at_certification"]


def test_profile_and_factory_are_explicit_without_contract_or_environment_drift():
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    assert len(create_review_app(dsn).openapi()["paths"]) == 6
    assert len(create_research_app(dsn).openapi()["paths"]) == 7
    m = manifest()
    candidate = revision(m)
    with pytest.raises(ValueError):
        verify_revision(candidate, m, {ROLES[2]: 1}, factory=RESEARCH_FACTORY)
    candidate["spec"]["containers"][0]["args"] = api_args(RESEARCH_FACTORY)
    verify_revision(candidate, m, {ROLES[2]: 1}, factory=RESEARCH_FACTORY)
    with pytest.raises(ValueError):
        api_args("unreviewed.module:factory")
    assert revision_name(m) != revision_name(m, RESEARCH_FACTORY)
    assert revision_name(m, RESEARCH_FACTORY) == revision_name(m) + "-research"


@pytest.mark.parametrize("fault", [None, "scope", "origin", "coverage", "cohort", "completion"])
def test_record_collector_requires_actual_immutable_execution_and_complete_scope(
    monkeypatch, fault
):
    m = manifest()
    origin = "https://service.a.run.app"
    evidence = {
        "level": "DEV_PRIVATE_HTTP",
        "endpoints": 6,
        "source_writes": 0,
        "insights_rbac_certified": False,
        "origin": origin,
        "audience": origin,
        "canary_versions": [1, 2],
        "coverage_observed": m.required_coverage,
        "cohort": [
            {"code": code, "result_id": "r" * 64, "input_hash": "i" * 64} for code in m.cohort
        ],
    }
    if fault == "scope":
        evidence["level"] = "RESTORED_CLONE_HTTP"
    if fault == "origin":
        evidence["origin"] = "https://other.a.run.app"
    if fault == "coverage":
        evidence["coverage_observed"] = []
    if fault == "cohort":
        evidence["cohort"] = evidence["cohort"][:1]
    document = seal_evidence(m, "certification", evidence)
    monkeypatch.setattr(
        "mfdataindia.review.image_provenance.verify_image",
        lambda *a, **kw: {"layers_downloaded": 0},
    )

    class Boundary:
        def service(self):
            return {
                "metadata": {"annotations": {"run.googleapis.com/ingress": "internal"}},
                "status": {"url": origin},
            }

        def inspect_execution(self, manifest, name, account, arguments, environment, secrets):
            assert name == "immutable-execution" and manifest == m
            assert environment["REVIEW_MANIFEST"] == m.model_dump_json()
            assert arguments == ["scripts/certify_verified_review_dev.py"]
            return {
                "status": {}
                if fault == "completion"
                else {"completionTime": "2024-01-02T00:00:00Z"}
            }, document

        def command(self, *args, **kwargs):
            pytest.fail("collector may not mutate cloud infrastructure")

    if fault:
        with pytest.raises(ValueError):
            publication_operations.verified_record(
                Boundary(), m, "immutable-execution", origin, {ROLES[1]: 1}
            )
    else:
        record = publication_operations.verified_record(
            Boundary(), m, "immutable-execution", origin, {ROLES[1]: 1}
        )
        assert record["certificate"] == document and record["manifest"] == m.model_dump(mode="json")


def install(dsn):
    with connection(dsn, readonly=True) as conn:
        before = publication_operations.capture_review(conn)
    with connection(dsn) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        result = publication_operations.install(conn, ROOT)
        assert result["original_source_rows_changed"] == 0
    with connection(dsn, readonly=True) as conn:
        after = publication_operations.capture_review(conn)
    publication_operations.compare_review(
        before, after, added_policy=fingerprint(publication.policy())
    )


def certificate(result, completed="2024-01-02T00:00:00Z"):
    m = manifest(cohort=[1], canary_code=1)
    cert = seal_evidence(
        m,
        "certification",
        {
            "level": "DEV_PRIVATE_HTTP",
            "endpoints": 6,
            "source_writes": 0,
            "insights_rbac_certified": False,
            "origin": "https://service.a.run.app",
            "audience": "https://service.a.run.app",
            "cohort": [
                {"code": 1, "result_id": result["result_id"], "input_hash": result["input_hash"]}
            ],
        },
    )
    return {
        "format": "verified-research-certification-v1",
        "publication_policy_hash": fingerprint(publication.policy()),
        "manifest": m.model_dump(mode="json"),
        "execution": {"status": {"completionTime": completed}},
        "certificate": cert,
    }


@pytest.mark.postgres
def test_optional_install_registration_acl_replay_and_source_preservation(verified_db):
    old = generate(verified_db)
    with connection(verified_db) as conn:
        before = source_state(conn)
    install(verified_db)
    document = certificate(old)
    with connection(verified_db) as conn:
        identifier = publication.register(conn, document)
        assert publication.register(conn, document) == identifier
        assert source_state(conn) == before
        assert not conn.execute(
            "SELECT has_table_privilege('mfdata_ingest','mf_review.certifications','SELECT,INSERT,UPDATE,DELETE') AS yes"
        ).fetchone()["yes"]
    for role in ROLES:
        with psycopg.connect(runtime_dsn(verified_db, role)) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("INSERT INTO mf_review.certifications VALUES('forged')")
        with connection(verified_db) as conn:
            assert not conn.execute(
                "SELECT has_table_privilege(%s,'mf_review.certifications','INSERT,UPDATE,DELETE,TRUNCATE') AS yes",
                (role,),
            ).fetchone()["yes"]
    with connection(verified_db, readonly=True) as conn:
        assert (
            publication.get_publication(conn, 1)["last_certified"]["certificate_id"] == identifier
        )
    with connection(verified_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM mf_review.certifications")


@pytest.mark.postgres
def test_caught_validation_failure_cannot_commit_partial_extension(verified_db, monkeypatch):
    with connection(verified_db) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        before = publication_operations.capture_review(conn)

        def reject(*args, **kwargs):
            raise ValueError("synthetic privilege failure after schema creation")

        monkeypatch.setattr(publication_operations, "verify_principal", reject)
        with pytest.raises(ValueError, match="synthetic privilege failure"):
            publication_operations.install(conn, ROOT)
        assert (
            conn.execute("SELECT to_regclass('mf_review.certifications') AS relation").fetchone()[
                "relation"
            ]
            is None
        )
        assert publication_operations.capture_review(conn) == before


@pytest.mark.postgres
def test_api_certified_context_snapshot_is_coherent_across_committed_refresh(verified_db):
    # Distinct synthetic variants establish a genuinely eligible Growth control.
    with connection(verified_db) as conn:
        conn.execute("UPDATE mf.nav_history SET nav=nav+1 WHERE amfi_scheme_code=2")
        certify_comparison_fixture(conn)
    old = generate(verified_db)
    assert old["eligible_at_capture"]
    install(verified_db)
    with connection(verified_db) as conn:
        publication.register(conn, certificate(old))
    with connection(verified_db, readonly=True) as reader:
        before = publication.get_publication(reader, 1)
        with connection(verified_db) as writer:
            writer.execute(
                "UPDATE mf.dataset_summary SET dataset_version=dataset_version+1 WHERE singleton"
            )
        assert publication.get_publication(reader, 1) == before
    private, verifier = trust()
    with TestClient(
        create_research_app(runtime_dsn(verified_db, ROLES[2]), verifier=verifier)
    ) as client:
        path = "/api/review/v1/funds/1/publication"
        assert client.get(path).status_code == 401
        response = client.get(
            path, headers={"Authorization": "Bearer " + assertion(private, scope=["review:read"])}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["current_dataset_version"] == old["dataset_version"] + 1
        assert body["last_certified"]["dataset_version"] == old["dataset_version"]
        assert (
            not body["current_comparison_eligible"]
            and response.headers["Cache-Control"] == "no-store"
        )
        assert body["state"] == "historical_certified"
        assert body["last_certified"]["metrics"] == old["metrics"]
        with connection(verified_db) as conn:
            conn.execute(
                "UPDATE mf.nav_history SET nav=99 WHERE amfi_scheme_code=1 AND nav_date='2024-01-01'"
            )
        changed = client.get(
            path, headers={"Authorization": "Bearer " + assertion(private, scope=["review:read"])}
        ).json()
        assert (
            changed["state"] == "historical_invalidated"
            and changed["last_certified"]["metrics"] is None
        )
        assert "historical_observations_changed" in changed["withheld_reasons"]
        with connection(verified_db, readonly=True) as conn:
            assert store.get_result(conn, 1, old["result_id"])["result"] == old
    for role in ROLES:
        with connection(runtime_dsn(verified_db, role)) as conn:
            verify_runtime(conn, "generator" if role == ROLES[0] else "api")


@pytest.mark.postgres
def test_bad_lineage_and_older_import_cannot_replace_newer_certification(verified_db):
    old = generate(verified_db)
    install(verified_db)
    newer = certificate(old, "2024-02-01T00:00:00Z")
    earlier = certificate(old, "2024-01-01T00:00:00Z")
    with connection(verified_db) as conn:
        chosen = publication.register(conn, newer)
        publication.register(conn, earlier)
    with connection(verified_db, readonly=True) as conn:
        assert publication.get_publication(conn, 1)["last_certified"]["certificate_id"] == chosen
    bad = deepcopy(newer)
    bad["certificate"]["evidence"]["cohort"][0]["input_hash"] = "0" * 64
    bad["certificate"]["sha256"] = fingerprint(
        {k: v for k, v in bad["certificate"].items() if k != "sha256"}
    )
    with connection(verified_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            publication.register(conn, bad)
