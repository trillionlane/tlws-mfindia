"""Synthetic regression cases are not issuer coverage or live acceptance."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest
from fastapi.testclient import TestClient
from mfdataindia.enrichment import (
    idcw_catalog as engine,
    idcw_store as store,
    idcw_operations as operations,
)
from mfdataindia.enrichment import (
    distribution_store as legacy,
    distributions,
    idcw_deployment as deployment,
)
from mfdataindia.enrichment.idcw_certification import projections, oracle_projection
from mfdataindia.review.app import create_idcw_catalog_app, create_distribution_observations_app
from mfdataindia.review.policy import fingerprint
from mfdataindia.review import store as review
from test_distribution_observations import prepare as prepare_legacy, append as append_legacy
from test_official_enrichment_store import verified_db as verified_db
from test_verified_review_store import connection, ROLES, source_state
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import trust, assertion

ROOT = Path(__file__).resolve().parents[1]
HEADER = "Record Date Regular Plan Direct Plan Cum IDCW NAV(₹) IDCW per Unit(₹) For Cum IDCW NAV(₹) IDCW per Unit(₹) For Individual & HUF Others Individual & HUF Others"
HISTORY = f"""121 | April 2026
HDFC FLEXI CAP FUND (Past 3 years) IDCW Option
{HEADER}
Mar 05, 26 79.8080 7.0000 7.0000 96.4400 6.0000 6.1000
HDFC LARGE CAP FUND (Past 3 years) IDCW Option
{HEADER}
Feb 12, 26 59.6610 5.5000 5.4000 70.5040 4.5000 4.4000
HDFC VALUE FUND (Past 3 years) IDCW Option
"""


def registry(name):
    flexi = name == "HDFC Flexi Cap Fund"
    term = "DIVIDEND" if flexi else "IDCW"
    regular, direct, growth1, growth2 = (
        (101763, 118954, 101762, 118955) if flexi else (102001, 119017, 102000, 119018)
    )
    reg, dr = engine.COHORT[regular], engine.COHORT[direct]
    rows = []
    for prefix, values in ((name, reg), (name + " - DIRECT PLAN", dr)):
        rows.extend(
            [
                f"{prefix} - {term} PAYOUT OPTION- {values[2]}",
                f"{prefix} - {term} REINVESTMENT OPTION- {values[3]}",
            ]
        )
    rows.extend(
        [
            f"{name} - GROWTH OPTION- " + ("INF179K01608" if flexi else "INF179K01BE2"),
            f"{name} - DIRECT PLAN - GROWTH OPTION- "
            + ("INF179K01UT0" if flexi else "INF179K01YV8"),
        ]
    )
    label = "IDCW Plan" if flexi else "IDCW Option - Regular Plan"
    return (
        f"1 Fund Name {name} 2 Option Names fixture 28 ISINs "
        + " ".join(rows)
        + f" 29 AMFI Codes (To be phased out) {regular} -{name} - {label} {direct} -{name} - IDCW Option - Direct Plan "
        + f"{growth1} -{name} - Growth Plan {growth2} -{name} - Growth Option - Direct Plan 30 SEBI Codes fixture"
    )


def inputs(monkeypatch):
    raw = {
        "history": b"%PDF-synthetic-history",
        "flexi": b"%PDF-synthetic-flexi",
        "large": b"%PDF-synthetic-large",
    }
    decoded = {
        raw["history"]: [{"page": 1, "text": "April 30, 2026"}, {"page": 121, "text": HISTORY}],
        raw["flexi"]: [{"page": 1, "text": registry("HDFC Flexi Cap Fund")}],
        raw["large"]: [{"page": 1, "text": registry("HDFC Large Cap Fund")}],
    }
    monkeypatch.setattr(engine.decoder, "decode", lambda content, selected: decoded[content])
    requests, sources = [], []
    for code in sorted(engine.COHORT):
        registry_key = "flexi" if "Flexi" in engine.COHORT[code][0] else "large"
        content = {"history": raw["history"], "registry": raw[registry_key]}
        request = {
            "format": "reported-idcw-request-v2",
            "adapter": engine.ADAPTER,
            "identity": engine.declared_identity(code),
            "proposed_start": "2025-04-01",
            "proposed_end": "2026-03-31",
            "history_period": "2026-04",
            "use_basis": "operator_reported_authorization",
            "authority_reference": "synthetic fixture only",
        }
        for role, data in content.items():
            filename = "history.pdf" if role == "history" else registry_key + ".pdf"
            request[role] = {
                "file": filename,
                "source_url": "https://files.hdfcfund.com/s3fs-public/" + filename,
                "content_hash": sha256(data).hexdigest(),
                "observed_at": "2026-10-15T10:00:00Z",
                "pages": [1, 121] if role == "history" else [1],
            }
        requests.append(engine.IdcwRequest.model_validate(request))
        sources.append(content)
    return requests, sources, decoded


def test_separate_plan_columns_classes_replay_and_frozen_v1(monkeypatch):
    requests, raw, _ = inputs(monkeypatch)
    original = distributions.policy()
    expected = {
        101763: ("79.8080", "7.0000", "7.0000"),
        118954: ("96.4400", "6.0000", "6.1000"),
        102001: ("59.6610", "5.5000", "5.4000"),
        119017: ("70.5040", "4.5000", "4.4000"),
    }
    for request, content in zip(requests, raw):
        doc = engine.derive(request, content)
        assert doc == engine.derive(request, content)
        row = doc["events"][0]
        assert (
            tuple(
                row[k]
                for k in ("cum_idcw_nav_inr", "amount_inr_per_unit", "amount_others_inr_per_unit")
            )
            == expected[doc["code"]]
        )
        assert doc["coverage_state"] == "unknown" and not doc["analytics_eligible"]
        assert row["reinvestment_nav_inr"] is None and doc["conditional_announcement"] is None
    assert distributions.policy() == original
    assert (
        fingerprint(original) == "9f56fce56365f7b014fdf7f4e718896b50b7545fe235eabaf0384624bc97581d"
    )


@pytest.mark.parametrize(
    "old,new",
    [
        ("Regular Plan Direct Plan", "Direct Plan Regular Plan"),
        ("IDCW per Unit(₹)", "IDCW (%)"),
        ("Mar 05, 26", "Feb 30, 26"),
        ("96.4400", "NaN"),
        ("6.0000 6.1000", "-6.00 6.10"),
        ("April 2026", "April 2025"),
    ],
)
def test_unsupported_history_never_falls_back(monkeypatch, old, new):
    requests, raw, decoded = inputs(monkeypatch)
    decoded[raw[0]["history"]][1]["text"] = HISTORY.replace(old, new)
    with pytest.raises(ValueError):
        engine.derive(requests[0], raw[0])


@pytest.mark.parametrize(
    "fault", ["plan", "code", "isins", "adapter", "window", "source_hash", "registry"]
)
def test_wrong_identity_or_evidence_rejected(monkeypatch, fault):
    requests, raw, decoded = inputs(monkeypatch)
    request = requests[0].model_dump(mode="json")
    if fault == "plan":
        request["identity"]["plan"] = "DIRECT"
    if fault == "code":
        request["identity"]["code"] = 101762
    if fault == "isins":
        request["identity"]["isins"].reverse()
    if fault == "adapter":
        request["adapter"] = "unreviewed"
    if fault == "window":
        request["proposed_start"] = "2024-01-01"
    if fault == "source_hash":
        request["history"]["content_hash"] = "0" * 64
    if fault == "registry":
        decoded[raw[0]["registry"]][0]["text"] = registry("HDFC Large Cap Fund")
    with pytest.raises(ValueError):
        engine.derive(engine.IdcwRequest.model_validate(request), raw[0])


def test_empty_selection_is_unknown_and_schema_forbids_eligibility(monkeypatch):
    requests, raw, _ = inputs(monkeypatch)
    body = requests[0].model_dump(mode="json") | {
        "proposed_start": "2026-04-01",
        "proposed_end": "2026-04-30",
    }
    doc = engine.derive(engine.IdcwRequest.model_validate(body), raw[0])
    assert doc["events"] == [] and doc["coverage_state"] == "unknown"
    for key in (
        "analytics_eligible",
        "complete_event_window_proven",
        "freshness_certified",
        "universe_complete",
        "source_data_replaced",
    ):
        with pytest.raises(ValueError):
            engine.IdcwSnapshot.model_validate(doc | {key: True})


def batch(requests):
    sources = {}
    for request in requests:
        for role in ("history", "registry"):
            source = getattr(request, role)
            sources[source.content_hash] = {
                "intake": source.model_dump(mode="json"),
                "object_name": f"official-idcw-catalog-rollouts/fixture/{source.content_hash}.pdf",
                "generation": "1",
                "byte_length": 100,
            }
    return deployment.IdcwBatch(requests=requests, sources=sources)


def test_bundle_bindings_refuse_extra_missing_wrong_metadata_and_objects(monkeypatch):
    requests, _, _ = inputs(monkeypatch)
    selected = batch(requests)
    manifest = SimpleNamespace(cohort=sorted(engine.COHORT), rollout_id="fixture")
    assert (
        deployment.selection(
            manifest,
            {"IDCW_BATCH": selected.model_dump_json(), "IDCW_BATCH_HASH": selected.bind(manifest)},
        )
        == selected
    )
    for fault in ("extra", "missing", "metadata", "object", "cohort", "duplicate"):
        body = selected.model_dump(mode="json")
        key = next(iter(body["sources"]))
        m = manifest
        if fault == "extra":
            body["sources"]["0" * 64] = body["sources"][key]
        if fault == "missing":
            del body["sources"][key]
        if fault == "metadata":
            body["sources"][key]["intake"]["observed_at"] = "2026-10-14T00:00:00Z"
        if fault == "object":
            body["sources"][key]["object_name"] = "wrong/object.pdf"
        if fault == "cohort":
            m = SimpleNamespace(cohort=[101763], rollout_id="fixture")
        if fault == "duplicate":
            body["requests"].append(body["requests"][0])
        with pytest.raises(ValueError):
            deployment.IdcwBatch.model_validate(body).bind(m)


def test_profile_additive_frozen_old_components_and_dormant_no_calls(monkeypatch):
    monkeypatch.delenv("MFDATAINDIA_REVIEW_TRUST_FILE", raising=False)
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    old, new = (
        create_distribution_observations_app(dsn).openapi(),
        create_idcw_catalog_app(dsn).openapi(),
    )
    assert len(new["paths"]) == 33
    for path, definition in old["paths"].items():
        assert new["paths"][path] == definition
    for name, definition in old["components"]["schemas"].items():
        assert new["components"]["schemas"][name] == definition
    with TestClient(create_idcw_catalog_app(dsn)) as client:
        assert client.get("/api/distributions/v2/inventory").status_code == 503
        assert client.post("/api/distributions/v2/policy").status_code == 405


def prepare(dsn, monkeypatch):
    request, raw, _ = prepare_legacy(dsn, monkeypatch)
    original = append_legacy(dsn, request, raw)
    with connection(dsn) as conn:
        amc = conn.execute(
            "SELECT amc_id FROM mf.amcs WHERE amfi_amc_name='HDFC Mutual Fund'"
        ).fetchone()["amc_id"]
        conn.execute(
            "INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type) VALUES(200002,%s,'Synthetic unsupported IDCW','SYNTHETIC UNSUPPORTED IDCW','OPEN_ENDED','Equity','REGULAR','IDCW')",
            (amc,),
        )
        for code in sorted(set(engine.COHORT) - {101763}):
            name, plan, payout, reinvest = engine.COHORT[code]
            conn.execute(
                "INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout,isin_div_reinvest) VALUES(%s,%s,%s,%s,'OPEN_ENDED','Equity',%s,'IDCW',%s,%s)",
                (code, amc, name, name.upper(), plan, payout, reinvest),
            )
    with connection(dsn) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        operations.install(conn, ROOT)
    return (*inputs(monkeypatch), original)


def append(dsn, requests, sources):
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        return store.append_batch(conn, requests, sources)


@pytest.mark.postgres
def test_pg_atomic_replay_preservation_actual_roles_and_originals(verified_db, monkeypatch):
    requests, raw, _, original = prepare(verified_db, monkeypatch)
    with connection(verified_db, readonly=True) as conn:
        before, prior = source_state(conn), operations.prior_layers(conn)
    docs = append(verified_db, requests, raw)
    assert append(verified_db, requests, raw) == docs
    with connection(verified_db, readonly=True) as conn:
        assert source_state(conn) == before and operations.prior_layers(conn) == prior
        assert (
            store.capture(conn)["sources"]["rows"] == 3
            and store.capture(conn)["snapshots"]["rows"] == 4
        )
        assert legacy.get(conn, 101763, original["snapshot_id"])["snapshot"] == original
        for table in ("sources", "snapshots"):
            assert not conn.execute(
                "SELECT has_table_privilege('mfdata_ingest',%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE') AS yes",
                ("mf_idcw." + table,),
            ).fetchone()["yes"]
    for role in ROLES:
        with connection(verified_db, role, readonly=True) as conn:
            store.verify_acl(conn)
            for doc in docs:
                assert store.get(conn, doc["code"], doc["snapshot_id"])["snapshot"] == doc
            if role != ROLES[0]:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute("SELECT content FROM mf_idcw.sources")
    for table in ("sources", "snapshots"):
        with connection(verified_db) as conn:
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("DELETE FROM mf_idcw." + table)


@pytest.mark.postgres
def test_pg_middle_batch_sql_failure_rolls_back_all_four(verified_db, monkeypatch):
    requests, raw, _, _ = prepare(verified_db, monkeypatch)
    with connection(verified_db) as conn:
        conn.execute(
            "UPDATE mf.funds SET scheme_name='changed synthetic identity' WHERE amfi_scheme_code=119017"
        )
    monkeypatch.setattr(
        store.evidence, "identity", lambda conn, code: engine.declared_identity(code)
    )
    with pytest.raises(psycopg.errors.RaiseException):
        append(verified_db, requests, raw)
    with connection(verified_db, readonly=True) as conn:
        assert (
            store.capture(conn)["sources"]["rows"] == store.capture(conn)["snapshots"]["rows"] == 0
        )


@pytest.mark.postgres
def test_pg_inventory_missing_unsupported_conflicts_and_identity_drift(verified_db, monkeypatch):
    requests, raw, decoded, _ = prepare(verified_db, monkeypatch)
    with connection(verified_db, ROLES[1], readonly=True) as conn:
        assert store.coverage(conn, 118954)["state"] == "not_captured"
        assert store.coverage(conn, 2)["state"] == "unsupported"
        assert store.coverage(conn, 2)["observed_record_dates"] is None
        with pytest.raises(review.NotFound):
            store.coverage(conn, 999999)
    docs = append(verified_db, requests, raw)
    # Different source observation conflicts with the legacy INR7 ceiling; no last-wins.
    revised_raw = dict(raw[0])
    revised_raw["history"] = b"%PDF-synthetic-conflicting-history"
    decoded[revised_raw["history"]] = deepcopy(decoded[raw[0]["history"]])
    decoded[revised_raw["history"]][1]["text"] = HISTORY.replace(
        "79.8080 7.0000 7.0000", "79.8080 8.0000 8.0000"
    )
    body = requests[0].model_dump(mode="json")
    body["history"]["content_hash"] = sha256(revised_raw["history"]).hexdigest()
    append(verified_db, [engine.IdcwRequest.model_validate(body)], [revised_raw])
    with connection(verified_db, ROLES[1], readonly=True) as conn:
        status = store.coverage(conn, 101763)
        assert status["state"] == "conflicting_evidence" and status["conflicting_record_dates"] == [
            "2026-03-05"
        ]
        assert len(status["snapshots"]) == 2 and len(status["legacy_snapshot_ids"]) == 1
        page, codes = store.inventory(conn, limit=1), []
        while True:
            codes += [item["code"] for item in page["items"]]
            assert all(
                item["coverage_state"] == "unknown" and not item["analytics_eligible"]
                for item in page["items"]
            )
            if page["next_after_code"] is None:
                break
            page = store.inventory(conn, after=page["next_after_code"], limit=1)
        assert codes == sorted(set(codes)) and set(engine.COHORT) <= set(codes)
        with pytest.raises(review.NotFound):
            store.get(conn, 118954, docs[0]["snapshot_id"])
    with connection(verified_db) as conn:
        conn.execute(
            "UPDATE mf.funds SET scheme_name='changed synthetic identity' WHERE amfi_scheme_code=118954"
        )
    with connection(verified_db, ROLES[1], readonly=True) as conn:
        chosen = next(d for d in docs if d["code"] == 118954)
        assert store.get(conn, 118954, chosen["snapshot_id"])["snapshot"] is None
        item = store.inventory(conn, after=118953, limit=1)["items"][0]
        assert item["state"] == "identity_changed" and item["snapshot_count"] is None
        assert engine.IdcwInventoryItem.model_validate(item).snapshot_count is None


@pytest.mark.postgres
def test_pg_http_and_certification_require_all_independent_per_plan_references(
    verified_db, monkeypatch
):
    from test_historical_store_api import foundations, install as install_historical

    foundations(verified_db)
    install_historical(verified_db)
    requests, raw, _, _ = prepare(verified_db, monkeypatch)
    docs = append(verified_db, requests, raw)
    selected = batch(requests)
    pins = {str(d["code"]): d["snapshot_id"] for d in docs}
    oracle = {str(d["code"]): oracle_projection(d) for d in docs}
    with connection(verified_db, ROLES[1], readonly=True) as conn:
        cases = projections(conn, selected, pins, oracle)
        for fault in ("missing", "wrong_plan", "omitted_null"):
            changed = deepcopy(oracle)
            if fault == "missing":
                del changed["119017"]
            if fault == "wrong_plan":
                changed["118954"]["events"] = changed["101763"]["events"]
            if fault == "omitted_null":
                del changed["101763"]["events"][0]["reinvestment_nav_inr"]
            with pytest.raises(ValueError):
                projections(conn, selected, pins, changed)
    key, verifier = trust()
    headers = {"Authorization": "Bearer " + assertion(key, scope=["review:read"])}
    app = create_idcw_catalog_app(runtime_dsn(verified_db, ROLES[1]), verifier=verifier)
    with TestClient(app) as client:
        for path, expected in cases:
            assert client.get(path).status_code == 401
            response = client.get(path, headers=headers)
            assert (
                response.status_code == 200
                and response.json() == expected
                and response.headers["Cache-Control"] == "no-store"
            )
        assert (
            client.get(
                "/api/distributions/v2/funds/101763/observations",
                headers=headers,
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/distributions/v2/inventory?limit=101",
                headers=headers,
            ).status_code
            == 422
        )
        assert client.post("/api/distributions/v2/policy", headers=headers).status_code == 405
        assert (
            client.get(
                "/api/distributions/v2/inventory?after_code=2147483648", headers=headers
            ).status_code
            == 422
        )
        assert client.get("/api/distributions/v2/sources/raw", headers=headers).status_code == 404


@pytest.mark.parametrize("after,limit", [(-1, 1), (2147483648, 1), (0, 0), (0, 101), (True, 1)])
def test_inventory_bounds_rejected_before_database(after, limit):
    with pytest.raises(ValueError):
        store.inventory(None, after=after, limit=limit)


@pytest.mark.postgres
@pytest.mark.parametrize(
    "field,value",
    [
        ("engine_code_hash", "0" * 64),
        ("analytics_eligible", True),
        ("complete_event_window_proven", True),
        ("coverage_state", "complete"),
        ("conditional_announcement", {}),
        ("identity_evidence", {}),
    ],
)
def test_pg_direct_insert_cannot_overclaim(verified_db, monkeypatch, field, value):
    requests, raw, _, _ = prepare(verified_db, monkeypatch)
    doc = append(verified_db, requests, raw)[0]
    changed = deepcopy(doc)
    changed[field] = value
    changed["snapshot_id"] = fingerprint({k: v for k, v in changed.items() if k != "snapshot_id"})
    with connection(verified_db, ROLES[0]) as conn:
        keys = conn.execute(
            "SELECT history_source_id,registry_source_id FROM mf_idcw.snapshots WHERE snapshot_id=%s",
            (doc["snapshot_id"],),
        ).fetchone()
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(
                "INSERT INTO mf_idcw.snapshots VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    changed["snapshot_id"],
                    changed["code"],
                    keys["history_source_id"],
                    keys["registry_source_id"],
                    changed["policy_hash"],
                    Jsonb(changed),
                ),
            )


@pytest.mark.postgres
def test_pg_capacity_preserves_exact_replay_and_rejects_new_snapshot(verified_db, monkeypatch):
    from datetime import date, timedelta

    requests, raw, _, _ = prepare(verified_db, monkeypatch)
    first = None
    for offset in range(100):
        body = requests[0].model_dump(mode="json")
        day = (date(2020, 1, 1) + timedelta(days=offset)).isoformat()
        body.update(proposed_start=day, proposed_end=day)
        selected = engine.IdcwRequest.model_validate(body)
        doc = append(verified_db, [selected], raw[:1])[0]
        if first is None:
            first = (selected, doc)
    assert append(verified_db, [first[0]], raw[:1]) == [first[1]]
    with pytest.raises(psycopg.errors.RaiseException, match="capacity"):
        append(verified_db, requests[:1], raw[:1])
    with connection(verified_db, readonly=True) as conn:
        assert store.capture(conn)["snapshots"]["rows"] == 100
        state = store.coverage(conn, 101763)
        assert state["coverage_state"] == "unknown" and state["observed_record_dates"] == []


@pytest.mark.postgres
def test_pg_column_grant_drift_and_runtime_install_refused(verified_db, monkeypatch):
    prepare(verified_db, monkeypatch)
    with connection(verified_db, ROLES[0]) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(ValueError, match="migration owner"):
            operations.install(conn, ROOT)
    with connection(verified_db) as conn:
        conn.execute("GRANT INSERT(metadata) ON mf_idcw.sources TO mfdata_ingest")
    with connection(verified_db, ROLES[1], readonly=True) as conn:
        with pytest.raises(ValueError, match="grants"):
            store.verify_acl(conn)
