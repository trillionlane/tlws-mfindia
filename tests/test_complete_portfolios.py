"""Synthetic controls and real PostgreSQL privileges; real disclosures certify separately."""

from copy import deepcopy
from decimal import localcontext
from pathlib import Path
import hashlib
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest
from fastapi.testclient import TestClient
from mfdataindia.enrichment import (
    portfolios as engine,
    portfolio_schemas as schemas,
    portfolio_store as store,
    portfolio_operations as operations,
)
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.app import create_portfolios_app, create_official_facts_app
from mfdataindia.review import store as review
from test_official_facts import identity, SUMMARY, LINEAGE, prepare as prepare_facts
from test_official_enrichment_store import verified_db as verified_db
from test_verified_review_store import connection, ROLES, source_state
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import trust, assertion

ROOT = Path(__file__).resolve().parents[1]


def row(label="", *, isin="", value="", weight="", qty="", fmt="#,##0.00%", prefix=""):
    vals = [prefix, "", label, isin, "", qty, value, weight, "", ""]
    return [
        {
            "value": str(v),
            "kind": "number" if i in (5, 6, 7) and v != "" and v != "$0.00%" else "text",
            "format": fmt if i == 7 else "General",
        }
        for i, v in enumerate(vals)
    ]


def fixture_rows():
    rows = [
        row(identity()["scheme_name"]),
        row("description"),
        row("Portfolio Statement as on August 31, 2026"),
        row(),
    ]
    for c, v in enumerate(
        [
            "Name of the Instrument / Issuer",
            "ISIN",
            "Industry^ / Rating",
            "Quantity",
            "Market value\n(Rs. in Lakhs)",
            "% to AUM",
        ],
        2,
    ):
        rows[3][c]["value"] = v
    rows += [
        row("Equity & Equity Related"),
        row("a) Listed/awaiting listing on Stock Exchanges"),
        row("ICICI Bank Ltd.", isin="INE090A01021", value="50.00", weight="0.5", qty="10.0"),
        row("HDFC Bank Ltd.", isin="INE040A01034", value="40.00", weight="0.4", qty="10.0"),
        row("Total", value="90.00", weight="0.9"),
        row("Others"),
        row("TREPS / Reverse Repo Investments"),
        row("TREPS", value="9.00", weight="0.09"),
        row("Total", value="9.00", weight="0.09"),
        row("Other Current Assets / (Liabilities)"),
        row("Margin amount for Derivative positions", value="2.00", weight="0.02"),
        row("Net Receivable / Payable", value="-1.00", weight="-0.01"),
        row("Total", value="1.00", weight="0.01"),
        row("GRAND TOTAL (AUM)", value="100.00", weight="1.0"),
        row("Regular Plan - Growth Option"),
    ]
    rows += [
        row(f"({p}) {label} as on August 31, 2026 is Nil.")
        for p, label in zip(
            "abcde",
            (
                "Hedging Position through Futures",
                "Other than Hedging Position through Futures",
                "Hedging Position through Options",
                "Other than Hedging Positions through Options",
                "Hedging Positions through Swaps",
            ),
            strict=True,
        )
    ]
    return rows


def parsed(rows=None, **kwargs):
    return engine.parse(
        kwargs.pop("identity", identity()),
        rows or fixture_rows(),
        kwargs.pop("registry", SUMMARY),
        as_of=kwargs.pop("as_of", "2026-08-31"),
        **kwargs,
    )


def test_complete_rows_signed_cash_exact_totals_and_deterministic_context():
    value = parsed()
    assert len(value["holdings"]) == 5
    assert value["grand_total_inr_lakh"] == "100.00"
    assert value["holdings"][-1]["market_value_inr_lakh"] == "-1.00"
    assert value["holdings"][0]["weight_percent"]["value"] == "50.0"
    assert value["overlap_eligible"] and not value["derivative_positions"]
    with localcontext() as context:
        context.prec = 6
        assert parsed() == value
    mutable = engine.policy()
    mutable["bounds"].clear()
    assert engine.policy()["bounds"]


@pytest.mark.parametrize(
    "change",
    [
        "date",
        "header",
        "subtotal",
        "grand",
        "missing_row",
        "unknown_row",
        "duplicate_id",
        "invalid_weight_format",
        "missing_subtotal",
        "future_note",
        "missing_note",
        "wrong_fund",
        "wrong_isin",
        "direct",
    ],
)
def test_inconsistent_partial_or_unmapped_source_cannot_publish(change):
    rows = fixture_rows()
    kw = {}
    if change == "date":
        kw["as_of"] = "2026-09-30"
    elif change == "header":
        rows[3][7]["value"] = "top-ten weights"
    elif change == "subtotal":
        rows[8][6]["value"] = "89.0"
    elif change == "grand":
        rows[17][6]["value"] = "99.0"
    elif change == "missing_row":
        rows.pop(7)
    elif change == "unknown_row":
        rows.insert(6, row("Unexpected section"))
    elif change == "duplicate_id":
        rows[7][3]["value"] = rows[6][3]["value"]
    elif change == "invalid_weight_format":
        rows[6][7]["format"] = "General"
    elif change == "missing_subtotal":
        rows.pop(8)
    elif change == "future_note":
        rows[-1][2]["value"] = rows[-1][2]["value"].replace("August", "September")
    elif change == "missing_note":
        rows.pop()
    elif change == "wrong_fund":
        kw["identity"] = identity(scheme_name="Other Fund")
    elif change == "wrong_isin":
        kw["identity"] = identity(isins=["INE040A01034"])
    elif change == "direct":
        kw["identity"] = identity(plan="DIRECT")
    with pytest.raises(ValueError):
        parsed(rows, **kw)


def test_censored_and_local_identifiers_preserved_not_fabricated_zero_or_isin():
    rows = fixture_rows()
    rows.insert(
        8, row("Unlisted legacy", isin="IDIA00028484", value="0.0", weight="$0.00%", qty="100.0")
    )
    value = parsed(rows)
    item = value["holdings"][2]
    assert item["identifier_kind"] == "issuer_local"
    assert item["weight_percent"]["value"] is None
    assert item["weight_percent"]["upper_bound_exclusive"] == "0.01"
    assert not value["overlap_eligible"]


def test_worker_invalid_zip_bounded_selection_and_timeout(monkeypatch):
    with pytest.raises(ValueError):
        engine.decode_archive(b"not a zip", ["BSLFEF"])
    with pytest.raises(ValueError):
        engine.decode_archive(b"PK", ["../unsafe"])
    import subprocess

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("worker", 35)

    monkeypatch.setattr(engine.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="deadline"):
        engine.decode_archive(b"PK", ["BSLFEF"])


def test_unparsed_numeric_derivative_note_cannot_hide_behind_nil_category():
    rows = fixture_rows()
    rows.insert(-3, row("Unparsed derivative", value="12.0", weight="0.12"))
    with pytest.raises(ValueError, match="unparsed numeric"):
        parsed(rows)


def test_frozen_prior_contract_and_dormant_new_private_profile():
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    old, new = create_official_facts_app(dsn).openapi(), create_portfolios_app(dsn).openapi()
    assert len(old["paths"]) == 18 and len(new["paths"]) == 22
    for key, value in old["paths"].items():
        assert new["paths"][key] == value
    for key, value in old["components"]["schemas"].items():
        assert new["components"]["schemas"][key] == value
    with TestClient(create_portfolios_app(dsn)) as client:
        assert client.get("/api/portfolios/v1/policy").status_code == 503
        assert client.post("/api/portfolios/v1/policy", json={}).status_code == 405


def import_source(dsn, content, *, pdf=False, suffix="fixture"):
    intake = schemas.Intake(
        source_url=(
            "https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_" + suffix + ".pdf"
            if pdf
            else "https://mutualfund.adityabirlacapital.com/-/media/bsl/files/resources/monthly-portfolio/"
            + suffix
            + ".zip"
        ),
        listing_url="https://mutualfund.adityabirlacapital.com/forms-and-downloads/portfolio",
        content_hash=hashlib.sha256(content).hexdigest(),
        media_type="application/pdf" if pdf else "application/zip",
        observed_at="2026-10-15T12:00:00Z",
        disclosure_as_of=None if pdf else "2026-08-31",
        use_basis="operator_reported_authorization",
        authority_reference="synthetic test authority",
    )
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        return store.append_source(conn, intake, content)


def prepare(dsn, monkeypatch):
    prepare_facts(dsn, monkeypatch)
    with connection(dsn) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        operations.install(conn, ROOT)
    rows = fixture_rows()
    monkeypatch.setattr(
        engine,
        "decode_archive",
        lambda content, sheets: {
            "sheets": {x: rows for x in sheets},
            "member_name": "fixture.xls",
            "member_hash": "a" * 64,
            "extractor_version": "synthetic",
        },
    )
    monkeypatch.setattr(engine, "decode", lambda *args: (SUMMARY, LINEAGE))
    source = import_source(dsn, b"PK\x03\x04fixture")
    summary = import_source(dsn, b"%PDF-fixture", pdf=True)
    return schemas.Selection(
        source_id=source,
        identity_source_id=summary,
        targets=[schemas.Target(code=1, sheet="BSLFEF", identity_page=28)],
    )


def publish(dsn, selection):
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        return store.publish(conn, selection)


@pytest.mark.postgres
def test_append_replay_privileges_immutability_and_pinned_pagination(verified_db, monkeypatch):
    selection = prepare(verified_db, monkeypatch)
    with connection(verified_db, readonly=True) as conn:
        before = source_state(conn)
    first = publish(verified_db, selection)
    assert publish(verified_db, selection) == first
    assert len(first) == 1
    with connection(verified_db) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        assert operations.install(conn, ROOT)["status"] == "ALREADY_INSTALLED"
        assert source_state(conn) == before
        assert not conn.execute(
            "SELECT has_table_privilege('mfdata_ingest','mf_portfolios.snapshots','INSERT') OR has_table_privilege('mfdata_ingest','mf_portfolios.sources','INSERT') AS allowed"
        ).fetchone()["allowed"]
    for role in ROLES[1:]:
        with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
            store.verify_acl(conn)
            response, _ = store.get(conn, 1)
            assert response["state"] == "available"
            sid = response["portfolio"]["snapshot_id"]
            page = store.holdings(conn, 1, sid, limit=2)
            assert len(page["items"]) == 2 and page["next_after_row"]
            assert len(store.holdings(conn, 1, sid, after=page["next_after_row"])["items"]) == 3
            assert store.snapshots(conn, 1)["items"] == [sid]
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT content FROM mf_portfolios.sources")
    with connection(verified_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM mf_portfolios.snapshots")
    with connection(verified_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("TRUNCATE mf_portfolios.sources CASCADE")


@pytest.mark.postgres
def test_conflict_stale_identity_cross_fund_and_database_seal(verified_db, monkeypatch):
    selection = prepare(verified_db, monkeypatch)
    first = publish(verified_db, selection)[0]
    other = import_source(verified_db, b"PK\x03\x04revised", suffix="revised")
    publish(verified_db, selection.model_copy(update={"source_id": other}))
    with connection(verified_db, readonly=True) as conn:
        assert store.get(conn, 1)[0]["state"] == "conflicting"
        assert store.get(conn, 1, snapshot_id=first["snapshot_id"])[0]["state"] == "available"
        with pytest.raises(review.NotFound):
            store.get(conn, 2, snapshot_id=first["snapshot_id"])
    forged = deepcopy(first)
    forged["identity_hash"] = "0" * 64
    forged["snapshot_id"] = fingerprint({k: v for k, v in forged.items() if k != "snapshot_id"})
    with connection(verified_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="seal"):
            conn.execute(
                "INSERT INTO mf_portfolios.snapshots VALUES(%s,1,%s,%s,%s,%s,%s)",
                (
                    forged["snapshot_id"],
                    selection.source_id,
                    selection.identity_source_id,
                    first["policy_hash"],
                    first["disclosure_as_of"],
                    Jsonb(forged),
                ),
            )
    with connection(verified_db) as conn:
        conn.execute("UPDATE mf.funds SET scheme_name='Changed' WHERE amfi_scheme_code=1")
    with connection(verified_db, readonly=True) as conn:
        assert (
            store.get(conn, 1, snapshot_id=first["snapshot_id"])[0]["state"] == "identity_changed"
        )
        with pytest.raises(review.Conflict):
            store.holdings(conn, 1, first["snapshot_id"])


@pytest.mark.postgres
def test_real_private_profile_auth_and_no_http_intake(verified_db, monkeypatch):
    from test_historical_store_api import foundations, install as install_historical

    foundations(verified_db)
    install_historical(verified_db)
    selection = prepare(verified_db, monkeypatch)
    snapshot = publish(verified_db, selection)[0]
    key, verifier = trust()
    headers = {"Authorization": "Bearer " + assertion(key, scope=["review:read"])}
    with TestClient(
        create_portfolios_app(runtime_dsn(verified_db, ROLES[2]), verifier=verifier)
    ) as client:
        path = "/api/portfolios/v1/funds/1/portfolio"
        assert client.get(path).status_code == 401
        result = client.get(path, headers=headers)
        assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
        assert result.json()["portfolio"]["holdings_count"] == 5
        assert "holdings" not in result.json()["portfolio"]
        assert "notes" not in result.json()["portfolio"]
        assert client.get(
            "/api/portfolios/v1/funds/1/snapshots/" + snapshot["snapshot_id"] + "/holdings?limit=2",
            headers=headers,
        ).json()["next_after_row"]
        assert client.post(path, headers=headers, json={}).status_code == 405
        assert client.get("/api/portfolios/v1/sources", headers=headers).status_code == 404


@pytest.mark.postgres
def test_batch_is_atomic_and_independent_oracle_rejects_false_pass(verified_db, monkeypatch):
    from mfdataindia.enrichment.portfolio_certification import projections

    selection = prepare(verified_db, monkeypatch)
    second = selection.model_copy(
        update={
            "targets": [
                *selection.targets,
                schemas.Target(code=2, sheet="BSLEQTY", identity_page=25),
            ]
        }
    )
    with pytest.raises(ValueError):
        publish(verified_db, second)
    with connection(verified_db, readonly=True) as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM mf_portfolios.snapshots").fetchone()["n"] == 0
        )
    document = publish(verified_db, selection)[0]
    oracle = {
        "1": {
            "holdings_count": 5,
            "grand_total_inr_lakh": "100.00",
            "holdings_digest": fingerprint(
                [
                    {k: h[k] for k in ("row", "name", "identifier", "market_value_inr_lakh")}
                    for h in document["holdings"]
                ]
            ),
            "derivative_count": 0,
            "source_content_hash": document["content_hash"],
            "identity_content_hash": document["identity_content_hash"],
            "disclosure_as_of": "2026-08-31",
        }
    }
    with connection(verified_db, readonly=True) as conn:
        assert len(projections(conn, selection, oracle)) == 5
        for key, bad in (
            ("holdings_count", 4),
            ("holdings_digest", "0" * 64),
            ("source_content_hash", "1" * 64),
        ):
            wrong = deepcopy(oracle)
            wrong["1"][key] = bad
            with pytest.raises(ValueError, match="oracle"):
                projections(conn, selection, wrong)
