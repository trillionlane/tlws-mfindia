"""Synthetic source/update/projection proofs; real source/DEV checks are separate."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from mfdataindia.api import queries
from mfdataindia.api.app import create_app
from mfdataindia.enrichment import current_facts as engine, current_facts_store as store
from mfdataindia.enrichment import current_facts_deployment as deployment
from mfdataindia.enrichment import current_facts_certification as certification
from mfdataindia.enrichment import facts, facts_schemas, facts_store, store as evidence
from mfdataindia.review.app import (
    create_current_facts_app,
    create_idcw_catalog_app,
    create_review_app,
)
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.preservation import capture_preservation, compare_preservation
from test_official_enrichment_evidence import intake
from test_official_facts import DETAIL, SUMMARY, LINEAGE, prepare
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import trust, assertion
from test_verified_review_store import ROLES, connection, source_state, verified_db as verified_db

ROOT = Path(__file__).resolve().parents[1]


def prepared(dsn, monkeypatch):
    selection = prepare(dsn, monkeypatch)
    with connection(dsn) as conn:
        conn.execute(
            "UPDATE mf.fund_facts SET benchmark='Legacy benchmark',benchmark_name='Legacy benchmark',fund_manager_name='Legacy Manager',base_expense_ratio=2.22,expense_ratio=2.50,risk_level='HIGH'"
        )
        original = source_state(conn)
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        facts_store.publish(conn, selection)
    with connection(dsn) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        result = store.install(conn, ROOT)
        assert result["status"] == "INSTALLED"
        assert not result["projection_active"]
    return selection, original


def switch(dsn, activate):
    with connection(dsn) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        return store.switch(conn, activate=activate)


def append_period(
    dsn,
    monkeypatch,
    selection,
    *,
    token,
    as_of,
    label,
    benchmark="Nifty 100 TRI",
    manager="Mr. New Manager",
    ber="1.25",
):
    detail = (
        DETAIL.replace("August 31, 2026", label)
        .replace("Nifty 100 TRI", benchmark)
        .replace("Mr. Harish Krishnan", manager)
        .replace("1.35%", ber + "%")
    )
    monkeypatch.setattr(
        facts,
        "decode",
        lambda content, page: (detail if page == 15 else SUMMARY, {**LINEAGE, "page": page}),
    )
    raw = b"%PDF-synthetic-period-" + token.encode()
    url = "https://mutualfund.adityabirlacapital.com/-/media/" + token + ".pdf"
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        record = evidence.append(
            conn,
            intake(
                raw,
                media_type="application/pdf",
                amc=facts.AMC,
                source_url=url,
                final_url=url,
                official_hosts=["mutualfund.adityabirlacapital.com"],
                codes=[1, 2],
                document_as_of=as_of,
            ),
            raw,
        )
    new = selection.model_copy(update={"document_id": record["document"]["document_id"]})
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        return facts_store.publish(conn, facts_schemas.Selection.model_validate(new.model_dump()))


def test_policy_copy_and_contract_compatibility():
    p = engine.policy()
    p["supported_fields"].clear()
    assert engine.policy()["supported_fields"]
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    old, new = create_idcw_catalog_app(dsn).openapi(), create_current_facts_app(dsn).openapi()
    assert len(old["paths"]) == 33 and len(new["paths"]) == 35
    for route in old["paths"]:
        assert old["paths"][route] == new["paths"][route]
    for key, value in old["components"]["schemas"].items():
        assert new["components"]["schemas"][key] == value
    with TestClient(create_current_facts_app(dsn)) as client:
        assert client.get("/api/enrichment/v3/policy").status_code == 503
        assert client.post("/api/enrichment/v3/funds/1/facts", json={}).status_code == 405


@pytest.mark.postgres
def test_http_certification_uses_restricted_reader_not_core_database_queries(
    verified_db, monkeypatch
):
    prepared(verified_db, monkeypatch)
    with connection(verified_db, readonly=True) as conn:
        source = store.get(conn, 1)["fields"]
        reviewed = {"1": {
            "snapshot_id": source["benchmark_name"]["sources"][0]["snapshot_id"],
            "as_of": source["benchmark_name"]["disclosure_as_of"],
            "values": {field: source[field]["value"] for field in engine.FIELDS},
        }}
    reader = runtime_dsn(verified_db, ROLES[1])
    with psycopg.connect(reader, row_factory=dict_row) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM mf.current_fund_facts LIMIT 1")
        conn.rollback()
        assert conn.execute("SELECT 1 FROM mf_live_facts.baseline LIMIT 1").fetchone()
    def forbidden(*args, **kwargs):
        raise AssertionError("certification must use private core HTTP, not owner core SQL")
    monkeypatch.setattr(queries, "fund_detail", forbidden)
    monkeypatch.setattr(queries, "funds_batch", forbidden)
    for active in (False, True, False):
        switch(verified_db, active)
        with psycopg.connect(reader, row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cases, core = deployment.projections(
                conn, reviewed, active=active, core_queries=False
            )
            assert len(cases) == 2
            assert core["1"] == {
                "benchmark_name": "Nifty 100 TRI" if active else "Legacy benchmark",
                "benchmark": "Nifty 100 TRI" if active else "Legacy benchmark",
                "fund_manager_name": "Mr. Harish Krishnan" if active else "Legacy Manager",
                "base_expense_ratio": 1.35 if active else 2.22,
            }


@pytest.mark.postgres
def test_actual_core_queries_advance_and_recover_without_source_rewrites(verified_db, monkeypatch):
    _, original = prepared(verified_db, monkeypatch)
    with connection(verified_db, readonly=True) as conn:
        before = queries.fund_detail(conn, 1)["facts"]
        assert before["benchmark_name"] == "Legacy benchmark"
        assert not store.get(conn, 1)["projection_active"]
    assert switch(verified_db, True)["projection_active"]
    with connection(verified_db, readonly=True) as conn:
        after = queries.fund_detail(conn, 1)["facts"]
        assert after["benchmark_name"] == after["benchmark"] == "Nifty 100 TRI"
        assert after["fund_manager_name"] == "Mr. Harish Krishnan"
        assert after["base_expense_ratio"] == 1.35
        # BER is not TER, and unchanged raw/computed columns stay exact.
        for key in before.keys() - {
            "benchmark",
            "benchmark_name",
            "fund_manager_name",
            "base_expense_ratio",
        }:
            assert after[key] == before[key]
        assert after["expense_ratio"] == 2.5 and after["risk_level"] == "HIGH"
        assert source_state(conn) == original
        response = engine.CurrentFactsResponse.model_validate(store.get(conn, 1))
        assert response.fields["base_expense_ratio"].verification_status == "needs_verification"
        assert response.fields["base_expense_ratio"].disclosure_as_of.isoformat() == "2026-08-31"
        assert not response.originals_replaced
    assert not switch(verified_db, False)["projection_active"]
    with connection(verified_db, readonly=True) as conn:
        assert queries.fund_detail(conn, 1)["facts"] == before
        assert source_state(conn) == original
        assert (
            conn.execute("SELECT count(*) AS n FROM mf_official_facts.snapshots").fetchone()["n"]
            == 2
        )
    assert switch(verified_db, True)["projection_active"]


@pytest.mark.postgres
def test_preservation_inspection_is_separate_read_only_and_requires_no_source_reference(
    verified_db, monkeypatch
):
    prepared(verified_db, monkeypatch)
    monkeypatch.setattr(deployment, "pinned_dsn", lambda *args: verified_db)
    manifest = SimpleNamespace(seal="f" * 64)
    real_capture = deployment.capture_preservation

    def checked_capture(conn, **kwargs):
        assert (
            conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        )
        assert conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"] == "5min"
        return real_capture(conn, **kwargs)

    monkeypatch.setattr(deployment, "capture_preservation", checked_capture)
    before = deployment.execute(manifest, {}, "inspect")["evidence"]
    switch(verified_db, True)
    after = deployment.execute(manifest, {}, "inspect")["evidence"]
    compare_preservation(before["preservation"], after["preservation"], "")
    assert before["read_only"] and after["read_only"]
    assert not before["current_facts"]["projection_active"]
    assert after["current_facts"]["projection_active"]
    assert before["current_view_definition_hash"] != after["current_view_definition_hash"]


@pytest.mark.postgres
def test_new_month_advances_existing_values_late_arrivals_cannot_revert_and_conflicts_are_per_field(
    verified_db, monkeypatch
):
    selection, original = prepared(verified_db, monkeypatch)
    switch(verified_db, True)
    newer = append_period(
        verified_db,
        monkeypatch,
        selection,
        token="sep",
        as_of="2026-09-30",
        label="September 30, 2026",
    )
    append_period(
        verified_db,
        monkeypatch,
        selection,
        token="jul-late",
        as_of="2026-07-31",
        label="July 31, 2026",
        ber="1.10",
    )
    with connection(verified_db, readonly=True) as conn:
        current = store.get(conn, 1)
        assert current["fields"]["base_expense_ratio"]["value"] == "1.25"
        assert current["fields"]["base_expense_ratio"]["disclosure_as_of"] == "2026-09-30"
        assert queries.fund_detail(conn, 1)["facts"]["base_expense_ratio"] == 1.25
        # History is retained, not overwritten by advancement.
        assert (
            conn.execute("SELECT count(*) AS n FROM mf_official_facts.snapshots").fetchone()["n"]
            == 6
        )
    append_period(
        verified_db,
        monkeypatch,
        selection,
        token="sep-conflict",
        as_of="2026-09-30",
        label="September 30, 2026",
        ber="1.30",
    )
    with connection(verified_db, readonly=True) as conn:
        current = store.get(conn, 1)
        assert current["fields"]["base_expense_ratio"]["state"] == "conflicting"
        assert current["fields"]["base_expense_ratio"]["value"] == "1.35"
        assert current["fields"]["base_expense_ratio"]["disclosure_as_of"] == "2026-08-31"
        assert current["fields"]["base_expense_ratio"]["latest_disclosure_as_of"] == "2026-09-30"
        assert current["fields"]["fund_managers"]["state"] == "available"
        assert current["fields"]["fund_managers"]["value"] == ["Mr. New Manager"]
        assert queries.fund_detail(conn, 1)["facts"]["fund_manager_name"] == "Mr. New Manager"
        assert source_state(conn) == original
        assert newer[0]["snapshot_id"] in {
            s["snapshot_id"] for s in current["fields"]["base_expense_ratio"]["unresolved_sources"]
        }


@pytest.mark.postgres
def test_identity_drift_no_cross_variant_borrowing_and_read_only_default_grants(
    verified_db, monkeypatch
):
    prepared(verified_db, monkeypatch)
    switch(verified_db, True)
    with connection(verified_db) as conn:
        conn.execute(
            "UPDATE mf.funds SET scheme_name=scheme_name||' changed' WHERE amfi_scheme_code=1"
        )
    for role in ROLES:
        with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
            store.verify_acl(conn)
            assert store.get(conn, 1)["fields"]["benchmark_name"]["state"] == "identity_changed"
            assert store.get(conn, 2)["fields"]["benchmark_name"]["state"] == "available"
            assert not conn.execute(
                "SELECT has_table_privilege(current_user,'mf_live_facts.configuration','INSERT,UPDATE,DELETE,TRUNCATE') AS allowed"
            ).fetchone()["allowed"]
    with connection(verified_db) as conn:
        assert not conn.execute(
            "SELECT has_table_privilege('mfdata_ingest','mf_live_facts.configuration','SELECT,INSERT,UPDATE,DELETE') AS allowed"
        ).fetchone()["allowed"]
        with pytest.raises(ValueError, match="direct projection owner"):
            with psycopg.connect(
                runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row
            ) as generator:
                generator.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                store.switch(generator, activate=False)


@pytest.mark.postgres
def test_install_replay_and_targeted_preservation_rejects_unrelated_structure_drift(
    verified_db, monkeypatch
):
    prepared(verified_db, monkeypatch)
    with connection(verified_db) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        assert store.install(conn, ROOT)["status"] == "ALREADY_INSTALLED"
        before = capture_preservation(conn, current_facts_baseline=True)
    switch(verified_db, True)
    with connection(verified_db) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        compare_preservation(before, capture_preservation(conn, current_facts_baseline=True), "")
        conn.execute("ALTER TABLE mf.fund_facts ADD COLUMN unexpected_current_fixture text")
        with pytest.raises(ValueError, match="source data or structure changed"):
            compare_preservation(
                before, capture_preservation(conn, current_facts_baseline=True), ""
            )


@pytest.mark.postgres
def test_private_metadata_values_are_published_without_waiting_for_human_review(
    verified_db, monkeypatch
):
    prepared(verified_db, monkeypatch)
    switch(verified_db, True)
    private, verifier = trust()
    app = create_review_app(
        runtime_dsn(verified_db, ROLES[1]), verifier=verifier, current_facts=True
    )
    with TestClient(app) as client:
        assert client.get("/api/enrichment/v3/funds/1/facts").status_code == 401
        # Existing assertion helpers use a separately generated synthetic key.
        response = client.get(
            "/api/enrichment/v3/funds/1/facts",
            headers={"Authorization": "Bearer " + assertion(private)},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["fields"]["benchmark_name"]["value"] == "Nifty 100 TRI"
        assert body["fields"]["benchmark_name"]["verification_status"] == "needs_verification"
        assert body["policy_hash"] == fingerprint(engine.policy())
        assert response.headers["cache-control"] == "no-store"


def test_migration_is_pinned_and_has_no_source_table_dml():
    import hashlib

    raw = (ROOT / "sql/optional" / store.MIGRATION).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == store.SCHEMA_SHA256
    assert not any(word in raw.decode() for word in ("UPDATE mf.", "DELETE FROM", "DROP "))
    assert "risk_level" not in engine.FIELDS
    assert "total_expense_ratio" not in engine.FIELDS
    assert deepcopy(engine.policy()) == engine.policy()


@pytest.mark.postgres
def test_recovery_restores_core_before_checks_of_drifted_candidate_views(verified_db, monkeypatch):
    prepared(verified_db, monkeypatch)
    switch(verified_db, True)
    with connection(verified_db) as conn:
        conn.execute(
            "CREATE OR REPLACE VIEW mf_live_facts.selected_fields AS SELECT DISTINCT ON(code,field) * FROM mf_live_facts.field_versions WHERE false ORDER BY code,field,as_of DESC"
        )
    with connection(verified_db, readonly=True) as conn:
        with pytest.raises(ValueError, match="view definitions differ"):
            store.verify_acl(conn)
    assert not switch(verified_db, False)["projection_active"]
    with connection(verified_db, readonly=True) as conn:
        assert queries.fund_detail(conn, 1)["facts"]["benchmark_name"] == "Legacy benchmark"
        assert not store.projection_state(conn)


@pytest.mark.postgres
def test_failed_post_switch_reference_rolls_back_activation_and_recovery_needs_no_reference(
    verified_db, monkeypatch
):
    prepared(verified_db, monkeypatch)
    monkeypatch.setattr(deployment, "pinned_dsn", lambda *args: verified_db)
    manifest = SimpleNamespace(seal="f" * 64, cohort=[1, 2])
    refs = {
        str(code): {
            "snapshot_id": "a" * 64,
            "as_of": "2026-08-31",
            "values": {
                "benchmark_name": "WRONG",
                "fund_managers": ["wrong"],
                "base_expense_ratio": "1",
            },
        }
        for code in (1, 2)
    }
    import json

    environment = {
        "CURRENT_FACTS_REFERENCE": json.dumps(refs),
        "CURRENT_FACTS_REFERENCE_HASH": fingerprint({"manifest": manifest.seal, "reference": refs}),
    }
    with pytest.raises(ValueError, match="independent original-source reference differs"):
        deployment.execute(manifest, environment, "activate")
    with connection(verified_db, readonly=True) as conn:
        assert not store.projection_state(conn)
    switch(verified_db, True)
    # Missing/corrupt source-reference evidence must never gate safety recovery.
    assert deployment.execute(manifest, {}, "recover")["evidence"]["projection_active"] is False


@pytest.mark.parametrize("change", ["hash", "cohort", "fields"])
def test_independent_reference_binding_is_required(change):
    import json

    manifest = SimpleNamespace(seal="f" * 64, cohort=[1])
    refs = {
        "1": {
            "snapshot_id": "a" * 64,
            "as_of": "2026-08-31",
            "values": {
                "benchmark_name": "Nifty 100 TRI",
                "fund_managers": ["Manager"],
                "base_expense_ratio": "1.35",
            },
        }
    }
    if change == "cohort":
        manifest.cohort = [2]
    elif change == "fields":
        del refs["1"]["values"]["fund_managers"]
    env = {
        "CURRENT_FACTS_REFERENCE": json.dumps(refs),
        "CURRENT_FACTS_REFERENCE_HASH": "a" * 64
        if change == "hash"
        else fingerprint({"manifest": manifest.seal, "reference": refs}),
    }
    with pytest.raises(ValueError):
        deployment.reference(manifest, env)


@pytest.mark.postgres
def test_numeric_spelling_is_corroboration_not_a_false_conflict(verified_db, monkeypatch):
    selection, _ = prepared(verified_db, monkeypatch)
    append_period(
        verified_db,
        monkeypatch,
        selection,
        token="same-value",
        as_of="2026-08-31",
        label="August 31, 2026",
        manager="Mr. Harish Krishnan",
        ber="1.3500",
    )
    switch(verified_db, True)
    with connection(verified_db, readonly=True) as conn:
        field = store.get(conn, 1)["fields"]["base_expense_ratio"]
        assert field["state"] == "available" and field["value"] == "1.35"
        assert field["source_count"] == 2
        assert {s["raw_value"] for s in field["sources"]} == {"1.35%", "1.3500%"}


@pytest.mark.postgres
@pytest.mark.parametrize(
    "field,value", [("base_expense_ratio", "NaN"), ("benchmark_name", " "), ("fund_managers", [])]
)
def test_malformed_accepted_legacy_assertion_cannot_break_current_api_or_erase_prior_value(
    verified_db, monkeypatch, field, value
):
    selection, _ = prepared(verified_db, monkeypatch)
    original = facts.derive

    def malformed(*args, **kwargs):
        result = original(*args, **kwargs)
        result["fields"][field]["value"] = value
        return result

    # Simulate the old ledger capability boundary independently of its parser.
    monkeypatch.setattr(facts, "derive", malformed)
    append_period(
        verified_db,
        monkeypatch,
        selection,
        token="unusable-" + field,
        as_of="2026-09-30",
        label="September 30, 2026",
    )
    switch(verified_db, True)
    with connection(verified_db, readonly=True) as conn:
        current = store.get(conn, 1)["fields"][field]
        assert current["state"] == "unusable"
        assert current["disclosure_as_of"] == "2026-08-31"
        assert current["latest_disclosure_as_of"] == "2026-09-30"
        assert current["reason"] == "unusable_disclosure_previous_official_retained"
        assert current["unresolved_source_count"] == 1
        assert queries.fund_detail(conn, 1)["facts"]["base_expense_ratio"] == (
            1.35 if field == "base_expense_ratio" else 1.25
        )


@pytest.mark.postgres
def test_official_values_work_for_a_code_with_no_original_facts_row(verified_db, monkeypatch):
    prepared(verified_db, monkeypatch)
    with connection(verified_db) as conn:
        conn.execute("DELETE FROM mf.fund_facts WHERE amfi_scheme_code=2")
        before = source_state(conn)
    switch(verified_db, True)
    with connection(verified_db, readonly=True) as conn:
        assert queries.fund_detail(conn, 2)["facts"]["benchmark_name"] == "Nifty 100 TRI"
        assert queries.funds_batch(conn, ["2"])["funds"][0]["base_expense_ratio"] == 1.35
        assert source_state(conn) == before
        assert not conn.execute("SELECT 1 FROM mf.fund_facts WHERE amfi_scheme_code=2").fetchone()


@pytest.mark.parametrize("change", ["none", "detail", "batch", "code", "status", "cache"])
def test_real_core_http_verifier_compares_selected_fields_without_backend_assertion_leak(
    monkeypatch, change
):
    monkeypatch.setattr(
        certification, "metadata_identity", lambda session, audience: "synthetic-iam-token"
    )
    expected = {
        "1": {
            "benchmark_name": "Nifty 100 TRI",
            "benchmark": "Nifty 100 TRI",
            "fund_manager_name": "Manager",
            "base_expense_ratio": 1.35,
        }
    }
    calls = []

    class Session:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            assert url.startswith(certification.CORE_ORIGIN + "/api/")
            assert kwargs["headers"] == {"Authorization": "Bearer synthetic-iam-token"}
            values = dict(expected["1"])
            batch = url.endswith("/batch")
            if change == "detail" and not batch or change == "batch" and batch:
                values["benchmark_name"] = "stale"
            body = (
                {"funds": [{**values, "amfi_scheme_code": 99 if change == "code" else 1}]}
                if batch
                else {"facts": values}
            )
            return SimpleNamespace(
                status_code=503 if change == "status" else 200,
                headers={"Cache-Control": "max-age=3600" if change == "cache" else "no-store"},
                json=lambda: body,
            )

    if change == "none":
        assert certification.core_http(Session(), expected) == 2
    else:
        with pytest.raises(ValueError):
            certification.core_http(Session(), expected)
    assert calls


@pytest.mark.postgres
def test_same_running_core_api_serves_next_official_month_without_restart(verified_db, monkeypatch):
    selection, _ = prepared(verified_db, monkeypatch)
    switch(verified_db, True)
    with TestClient(create_app(verified_db)) as client:
        first = client.get("/api/funds/1")
        assert first.status_code == 200
        assert first.json()["facts"]["base_expense_ratio"] == 1.35
        append_period(
            verified_db,
            monkeypatch,
            selection,
            token="http-next-month",
            as_of="2026-09-30",
            label="September 30, 2026",
        )
        second = client.get("/api/funds/1")
        batch = client.get("/api/funds/batch?ids=1")
        assert second.status_code == batch.status_code == 200
        assert second.json()["facts"]["base_expense_ratio"] == 1.25
        assert batch.json()["funds"][0]["base_expense_ratio"] == 1.25
        assert (
            first.headers["cache-control"]
            == second.headers["cache-control"]
            == batch.headers["cache-control"]
            == "no-store"
        )


@pytest.mark.postgres
def test_hot_view_switch_never_performs_a_bulk_preservation_copy(verified_db, monkeypatch):
    prepared(verified_db, monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "bulk source copy inside projection transaction can hold the hot view write lock"
        )

    monkeypatch.setattr(store, "capture_preservation", forbidden)
    assert switch(verified_db, True)["projection_active"]
    assert not switch(verified_db, False)["projection_active"]


@pytest.mark.postgres
def test_installation_checks_structure_without_bulk_data_locks_or_false_row_certificate(
    verified_db, monkeypatch
):
    from mfdataindia.review.preservation import compare_preservation_structure

    actual = store.capture_preservation
    seen = []

    def structure_only(conn, **kwargs):
        assert kwargs.get("structure_only") is True
        assert not conn.execute(
            "SELECT 1 FROM pg_locks l JOIN pg_class c ON c.oid=l.relation JOIN pg_namespace n ON n.oid=c.relnamespace WHERE l.pid=pg_backend_pid() AND n.nspname='mf' AND l.mode='ShareLock' LIMIT 1"
        ).fetchone()
        result = actual(conn, **kwargs)
        seen.append(result)
        return result

    monkeypatch.setattr(store, "capture_preservation", structure_only)
    prepared(verified_db, monkeypatch)
    assert len(seen) == 2 and all(s["tables"] == {} for s in seen)
    compare_preservation_structure(*seen)
    with pytest.raises(ValueError, match="invalid preservation evidence"):
        compare_preservation(*seen, "")
    with connection(verified_db, readonly=True) as conn:
        full = capture_preservation(conn)
        assert (
            full["format"] == "mfdata-preservation-v1" and full["tables"]["nav_history"]["rows"] > 0
        )
        assert full["structure_hash"] == seen[-1]["structure_hash"]
    with pytest.raises(ValueError, match="structure-only"):
        compare_preservation_structure(full, full)
