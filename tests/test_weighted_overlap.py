"""Independent mathematical references and synthetic PG safety, not live acceptance."""

from copy import deepcopy
from decimal import localcontext
from pathlib import Path
import pytest
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi.testclient import TestClient
from mfdataindia.analytics import (
    overlap as engine,
    overlap_store as store,
    overlap_schemas as schemas,
    overlap_operations as operations,
)
from mfdataindia.analytics.overlap_certification import projections, oracle_projection
from mfdataindia.enrichment import store as evidence
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.app import create_overlap_app, create_portfolios_app
from test_complete_portfolios import prepare as prepare_portfolios, publish as publish_portfolios
from test_official_enrichment_store import verified_db as verified_db
from test_verified_review_store import connection, ROLES, source_state
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import trust, assertion

ROOT = Path(__file__).resolve().parents[1]
A, B, C = "INE090A01021", "INE040A01034", "INE009A01021"


def holding(isin=A, weight="60", *, row=1, kind="isin", asset="equity", censored=False):
    return {
        "row": row,
        "name": "Synthetic " + (isin or "Cash"),
        "asset_class": asset,
        "identifier": isin,
        "identifier_kind": kind,
        "weight_percent": {
            "value": None if censored else weight,
            "basis": "censored" if censored else "reported",
            "upper_bound_exclusive": "0.01" if censored else None,
        },
    }


def snapshot(code, holdings, **changes):
    return {
        "code": code,
        "snapshot_id": str(code) * 64,
        "disclosure_as_of": "2026-08-31",
        "statement_complete": True,
        "overlap_eligible": False,
        "derivative_positions": [],
        "holdings": holdings,
        **changes,
    }


def test_independent_min_weight_reference_and_partial_concentration():
    a = snapshot(
        1,
        [
            holding(A, "60"),
            holding(B, "30", row=2),
            holding(None, "10", row=3, kind="not_applicable", asset="cash"),
        ],
    )
    b = snapshot(2, [holding(A, "40"), holding(B, "10", row=2), holding(C, "50", row=3)])
    result = engine.derive(a, b)
    # Hand-derived: min(60,40)+min(30,10)=50, not Jaccard 2/3 or 90-normalized.
    assert result["observed_overlap_percent"] == "50.00000000"
    assert result["shared_security_count"] == 2
    assert result["coverage"][0]["observed_hhi_contribution"] == "0.450000000000"
    assert result["coverage"][0]["top5_observed_weight_percent"] == "90.00000000"
    assert result["full_economic_overlap_percent"] is None
    assert engine.derive(b, a) == result
    with localcontext() as context:
        context.prec = 3
        assert engine.derive(a, b) == result
    mutable = engine.policy()
    mutable["bounds"].clear()
    assert engine.policy()["bounds"]


@pytest.mark.parametrize(
    "right,expected",
    [
        ([holding(A, "60"), holding(B, "40", row=2)], "100.00000000"),
        ([holding(C, "100")], "0.00000000"),
    ],
)
def test_identical_and_disjoint_reference(right, expected):
    result = engine.derive(
        snapshot(1, [holding(A, "60"), holding(B, "40", row=2)]), snapshot(2, right)
    )
    assert result["observed_overlap_percent"] == expected
    assert result["full_economic_state"] == "unavailable"


def test_censored_local_derivatives_and_signed_liabilities_never_imputed():
    a = snapshot(
        1,
        [
            holding(A, "40"),
            holding(B, row=2, censored=True),
            holding("IDIA00028484", "0", row=3, kind="issuer_local"),
            holding(None, "-1", row=4, asset="current_assets_liabilities", kind="not_applicable"),
        ],
        derivative_positions=[{"margin": "5"}],
    )
    result = engine.derive(a, snapshot(2, [holding(A, "20"), holding(B, "50", row=2)]))
    assert result["observed_overlap_percent"] == "20.00000000"
    coverage = result["coverage"][0]
    assert len(coverage["excluded_rows"]) == 3 and coverage["derivative_positions_excluded"] == 1
    assert coverage["excluded_rows"][0]["reported_weight_percent"] is None
    assert coverage["excluded_rows"][0]["upper_bound_exclusive"] == "0.01"
    assert coverage["excluded_rows"][-1]["reported_weight_percent"] == "-1"
    empty = engine.derive(snapshot(1, [holding(A, censored=True)]), snapshot(2, [holding(B)]))
    assert empty["observed_state"] == "unavailable" and empty["observed_overlap_percent"] is None
    assert empty["coverage"][0]["concentration_state"] == "unavailable"
    assert empty["coverage"][0]["observed_hhi_contribution"] is None


@pytest.mark.parametrize(
    "failure", ["date", "partial", "duplicate", "negative", "leverage", "class", "nan", "row_bound"]
)
def test_unsupported_inputs_fail_closed(failure):
    a, b = snapshot(1, [holding()]), snapshot(2, [holding()])
    if failure == "date":
        b["disclosure_as_of"] = "2026-07-31"
    elif failure == "partial":
        a["statement_complete"] = False
    elif failure == "duplicate":
        a["holdings"].append(holding(row=2))
    elif failure in ("negative", "leverage", "nan"):
        a["holdings"][0]["weight_percent"]["value"] = {
            "negative": "-1",
            "leverage": "101",
            "nan": "NaN",
        }[failure]
    elif failure == "class":
        b["holdings"][0]["asset_class"] = "debt"
    else:
        a["holdings"] *= 1001
    with pytest.raises(ValueError):
        engine.derive(a, b)


def test_only_final_sum_is_rounded_and_ambient_context_does_not_leak():
    a = snapshot(1, [holding(A, "0.000000005"), holding(B, "0.000000005", row=2)])
    b = snapshot(2, deepcopy(a["holdings"]))
    assert engine.derive(a, b)["observed_overlap_percent"] == "0.00000001"


def test_engine_lineage_includes_decimal_isin_and_canonical_helpers(monkeypatch):
    before = engine.engine_hash()
    original = Path.read_bytes
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path: (
            original(path) + (b"\n# changed checksum helper" if path.name == "evidence.py" else b"")
        ),
    )
    assert engine.engine_hash() != before


def test_frozen_prior_contract_dormant_profile_and_pair_validation():
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    old, new = create_portfolios_app(dsn).openapi(), create_overlap_app(dsn).openapi()
    assert len(new["paths"]) == 26
    for key, value in old["paths"].items():
        assert new["paths"][key] == value
    for key, value in old["components"]["schemas"].items():
        assert new["components"]["schemas"][key] == value
    with TestClient(create_overlap_app(dsn)) as client:
        assert client.get("/api/overlap/v1/policy").status_code == 503
    with pytest.raises(ValueError):
        schemas.Pair(
            left_code=2, right_code=1, left_snapshot_id="a" * 64, right_snapshot_id="b" * 64
        )


def prepare(dsn, monkeypatch):
    selected = prepare_portfolios(dsn, monkeypatch)
    first = publish_portfolios(dsn, selected)[0]
    # Explicit synthetic second portfolio. No official source data is forged or
    # published by this fixture outside its disposable database.
    with connection(dsn) as conn:
        conn.execute(
            "UPDATE mf.funds SET scheme_name=%s,option_type='GROWTH',isin_growth_or_div_payout=%s WHERE amfi_scheme_code=2",
            (first["identity"]["scheme_name"], first["identity"]["isins"][0]),
        )
        identity = evidence.identity(conn, 2)
        second = deepcopy(first)
        second.update(code=2, identity=identity, identity_hash=fingerprint(identity))
        second["identity_evidence"]["amfi_code"] = 2
        second["snapshot_id"] = fingerprint({k: v for k, v in second.items() if k != "snapshot_id"})
        conn.execute(
            "INSERT INTO mf_portfolios.snapshots VALUES(%s,2,%s,%s,%s,%s,%s)",
            (
                second["snapshot_id"],
                second["source_id"],
                second["identity_source_id"],
                second["policy_hash"],
                second["disclosure_as_of"],
                Jsonb(second),
            ),
        )
    with connection(dsn) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        operations.install(conn, ROOT)
    return schemas.Pair(
        left_code=1,
        right_code=2,
        left_snapshot_id=first["snapshot_id"],
        right_snapshot_id=second["snapshot_id"],
    )


def publish(dsn, pair):
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        return store.publish(conn, pair)


@pytest.mark.postgres
def test_replay_default_grants_effective_roles_source_preservation_and_db_seal(
    verified_db, monkeypatch
):
    pair = prepare(verified_db, monkeypatch)
    with connection(verified_db, readonly=True) as conn:
        before = source_state(conn)
    first = publish(verified_db, pair)
    assert publish(verified_db, pair) == first
    with connection(verified_db) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        assert operations.install(conn, ROOT)["status"] == "ALREADY_INSTALLED"
        assert source_state(conn) == before
        assert not conn.execute(
            "SELECT has_table_privilege('mfdata_ingest','mf_overlap.results','SELECT,INSERT,UPDATE,DELETE') AS allowed"
        ).fetchone()["allowed"]
    for role in ROLES:
        with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
            store.verify_acl(conn)
    with connection(verified_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("TRUNCATE mf_overlap.results")
    with psycopg.connect(runtime_dsn(verified_db, ROLES[1])) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM mf_overlap.results")
    forged = {**first, "left_snapshot_id": pair.right_snapshot_id}
    forged["result_id"] = fingerprint({k: v for k, v in forged.items() if k != "result_id"})
    with connection(verified_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="seal"):
            conn.execute(
                "INSERT INTO mf_overlap.results VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    forged["result_id"],
                    pair.right_snapshot_id,
                    pair.right_snapshot_id,
                    first["policy_hash"],
                    first["engine_code_hash"],
                    Jsonb(forged),
                ),
            )


@pytest.mark.postgres
def test_current_pins_identity_change_pagination_and_false_pass_oracle(verified_db, monkeypatch):
    pair = prepare(verified_db, monkeypatch)
    with connection(verified_db, readonly=True) as conn:
        assert store.get(conn, 1, 2)[0]["state"] == "not_published"
    value = publish(verified_db, pair)
    with connection(verified_db, readonly=True) as conn:
        response = store.get(conn, 1, 2)[0]
        assert response["result"]["observed_overlap_percent"] == "90.00000000"
        assert store.get(conn, 2, 1)[0] == response
        page = store.shared(conn, value["result_id"], limit=1)
        assert page["next_after_isin"]
        assert (
            len(store.shared(conn, value["result_id"], after=page["next_after_isin"])["items"]) == 1
        )
        assert projections(conn, pair, oracle_projection(value))
        bad = {**oracle_projection(value), "observed_overlap_percent": "91.00000000"}
        with pytest.raises(ValueError, match="oracle"):
            projections(conn, pair, bad)
        with pytest.raises(ValueError):
            store.get(conn, 1, 2, left_snapshot_id=pair.left_snapshot_id)
    with connection(verified_db) as conn:
        conn.execute("UPDATE mf.funds SET scheme_name='Changed' WHERE amfi_scheme_code=1")
    with connection(verified_db, readonly=True) as conn:
        assert store.get(conn, 1, 2)[0]["state"] == "inputs_unavailable"
        assert (
            store.get(
                conn,
                1,
                2,
                left_snapshot_id=pair.left_snapshot_id,
                right_snapshot_id=pair.right_snapshot_id,
            )[0]["result"]
            is None
        )
        with pytest.raises(review.Conflict):
            store.shared(conn, value["result_id"])


@pytest.mark.postgres
@pytest.mark.parametrize("new_date", ["2026-08-31", "2026-09-30"])
def test_current_conflict_or_newer_date_cannot_serve_old_result_but_explicit_pins_can(
    verified_db, monkeypatch, new_date
):
    from mfdataindia.enrichment import portfolio_store, portfolio_schemas

    pair = prepare(verified_db, monkeypatch)
    first = publish(verified_db, pair)
    with connection(verified_db, readonly=True) as conn:
        snapshot = deepcopy(portfolio_store.get(conn, 1)[1])
        intake = conn.execute(
            "SELECT intake FROM mf_portfolios.sources WHERE source_id=%s", (snapshot["source_id"],)
        ).fetchone()["intake"]
    payload = b"PK\x03\x04second-synthetic-disclosure"
    import hashlib

    revised = portfolio_schemas.Intake.model_validate(
        {
            **intake,
            "disclosure_as_of": new_date,
            "content_hash": hashlib.sha256(payload).hexdigest(),
        }
    )
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        source_id = portfolio_store.append_source(conn, revised, payload)
    snapshot.update(
        source_id=source_id, content_hash=revised.content_hash, disclosure_as_of=new_date
    )
    snapshot["snapshot_id"] = fingerprint({k: v for k, v in snapshot.items() if k != "snapshot_id"})
    with connection(verified_db) as conn:
        conn.execute(
            "INSERT INTO mf_portfolios.snapshots VALUES(%s,1,%s,%s,%s,%s,%s)",
            (
                snapshot["snapshot_id"],
                source_id,
                snapshot["identity_source_id"],
                snapshot["policy_hash"],
                new_date,
                Jsonb(snapshot),
            ),
        )
    with connection(verified_db, readonly=True) as conn:
        response = store.get(conn, 1, 2)[0]
        assert response["state"] == "inputs_unavailable"
        assert response["reason"] == (
            "portfolio_conflicting" if new_date == "2026-08-31" else "portfolio_dates_differ"
        )
        historical = store.get(
            conn,
            1,
            2,
            left_snapshot_id=pair.left_snapshot_id,
            right_snapshot_id=pair.right_snapshot_id,
        )[0]
        assert historical["selection"] == "explicit_historical_pins"
        assert historical["result"]["result_id"] == first["result_id"]


@pytest.mark.postgres
def test_private_http_26_routes_no_writer_and_no_raw_source(verified_db, monkeypatch):
    from test_historical_store_api import foundations, install as install_historical

    foundations(verified_db)
    install_historical(verified_db)
    pair = prepare(verified_db, monkeypatch)
    result = publish(verified_db, pair)
    key, verifier = trust()
    headers = {"Authorization": "Bearer " + assertion(key, scope=["review:read"])}
    with TestClient(
        create_overlap_app(runtime_dsn(verified_db, ROLES[2]), verifier=verifier)
    ) as client:
        route = "/api/overlap/v1/pairs/1/2"
        assert client.get(route).status_code == 401
        response = client.get(route, headers=headers)
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        assert response.json()["result"]["full_economic_overlap_percent"] is None
        assert "shared_holdings" not in response.json()["result"]
        assert (
            client.get(
                "/api/overlap/v1/results/" + result["result_id"], headers=headers
            ).status_code
            == 200
        )
        assert client.get(
            "/api/overlap/v1/results/" + result["result_id"] + "/shared-holdings?limit=1",
            headers=headers,
        ).json()["next_after_isin"]
        assert (
            client.get(
                route + "?left_snapshot_id=" + pair.left_snapshot_id, headers=headers
            ).status_code
            == 422
        )
        assert client.get("/api/overlap/v1/pairs/1/1", headers=headers).status_code == 422
        assert (
            client.get(
                route
                + "?left_snapshot_id="
                + pair.right_snapshot_id
                + "&right_snapshot_id="
                + pair.left_snapshot_id,
                headers=headers,
            ).status_code
            == 404
        )
        assert client.post(route, headers=headers, json={}).status_code == 405
        assert client.get("/api/overlap/v1/sources", headers=headers).status_code == 404
