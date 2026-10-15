"""Synthetic layout/PG proofs; actual source/DEV acceptance is a separate gate."""
from copy import deepcopy
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from mfdataindia.enrichment import facts, facts_store, facts_schemas, facts_operations
from mfdataindia.enrichment import store as evidence
from mfdataindia.review import store as review
from mfdataindia.review.app import create_enrichment_app, create_official_facts_app
from mfdataindia.review.policy import fingerprint
from test_official_enrichment_store import install as install_evidence, verified_db as verified_db
from test_official_enrichment_evidence import intake
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import trust, assertion
from test_verified_review_store import ROLES, connection, source_state

ROOT = Path(__file__).resolve().parents[1]
NAME = "Aditya Birla Sun Life Large Cap Fund"
ISIN = "INF209K01BR9"
DETAIL = f"""{NAME}
Fund Details
Benchmark Name Nifty 100 TRI
NAV as on August 31, 2026
Regular Plan - Growth Option ₹ 523.24
Regular Plan - Annual IDCW Option ₹ 38.94
AUM as on August 31, 2026 29,710.82
Fund Manager Details
Name of Fund Managers
• Mr. Harish Krishnan
Total Experience 22
Expense Ratio
Regular Plan: BER 1.35%
Direct Plan: BER 0.81%
"""
SUMMARY = f"""SCHEME SUMMARY DOCUMENT
1 Fund Name {NAME}
ISINs
{NAME}-Regular - IDCW INF209K01BO6
{NAME}-Growth {ISIN}
AMFI Codes (To be phased out)
{NAME}-Regular - IDCW 2
{NAME}-Growth 1
30 SEBI Codes fixture
"""
LINEAGE = {"extractor_version": "synthetic", "extraction_policy_hash": "1" * 64,
           "extraction_engine_hash": "2" * 64, "text_hash": "3" * 64, "page": 15}


def identity(**changes):
    return {"code": 1, "amc": facts.AMC, "scheme_name": NAME,
            "plan": "REGULAR", "option": "GROWTH", "isins": [ISIN], **changes}


def parsed(**changes):
    return facts.parse(changes.pop("identity", identity()), changes.pop("detail", DETAIL),
                       changes.pop("registry", SUMMARY), as_of=changes.pop("as_of", "2026-08-31"),
                       detail_lineage=LINEAGE, identity_lineage={**LINEAGE, "page": 28}, **changes)


def test_exact_disclosures_and_no_ber_ter_or_aum_unit_inference():
    result = parsed()
    assert result == parsed()
    assert result["fields"]["base_expense_ratio"]["value"] == "1.35"
    assert result["fields"]["base_expense_ratio"]["unit"] == "percent"
    assert result["fields"]["fund_managers"]["value"] == ["Mr. Harish Krishnan"]
    assert result["fields"]["benchmark_name"]["value"] == "Nifty 100 TRI"
    assert result["fields"]["aum"]["value"] is None
    assert result["fields"]["total_expense_ratio"]["reason"] == "BER_is_not_TER"
    assert all(v["date_basis"] == "document_context_not_field_effective_date" for v in result["fields"].values())
    p = facts.policy()
    p["fields"].clear()
    assert facts.policy()["fields"]


@pytest.mark.parametrize("change", [
    {"identity": identity(amc="Other AMC")}, {"identity": identity(plan="DIRECT")},
    {"identity": identity(option="UNKNOWN")}, {"identity": identity(code=99)},
    {"identity": identity(isins=["INF209K01YY7"])}, {"identity": identity(scheme_name=NAME + " II")},
    {"as_of": "2026-09-30"}, {"detail": DETAIL + "Benchmark Name Other TRI\n"},
    {"detail": DETAIL.replace("BER", "TER")}, {"registry": SUMMARY.replace(ISIN, "INF209K01BR8")},
    {"registry": SUMMARY.replace("-Growth " + ISIN, "-Growth " + ISIN + "\n" + NAME + "-Growth " + ISIN)},
    {"detail": DETAIL.replace("1.35%", "99.35%")},
])
def test_ambiguous_or_changed_inputs_fail_closed(change):
    with pytest.raises(ValueError):
        parsed(**change)


def test_exact_idcw_mapping_never_unlocks_adjusted_analytics_or_reinvestment_identity():
    value = parsed(identity=identity(code=2, option="IDCW", isins=["INF209K01BO6", "INF209K01EC5"]))
    assert value["identity_evidence"]["matched_payout_isin"] == "INF209K01BO6"
    assert value["identity_evidence"]["reinvestment_isin_verified"] is False
    assert "analytics" not in value


def test_pdf_decoder_uses_text_not_page_metadata(monkeypatch):
    monkeypatch.setattr(facts, "extract_pdf", lambda *a, **kw: {"pages": [{"text": "page text", "text_hash": "a" * 64}], "extractor_version": "fixture", "policy_hash": "b" * 64, "engine_code_hash": "c" * 64})
    assert facts.decode(b"fixture", 15)[0] == "page text"


def test_duplicate_code_under_other_label_is_ambiguous():
    registry = SUMMARY.replace(NAME + "-Regular - IDCW 2", NAME + "-Regular - IDCW 1")
    with pytest.raises(ValueError, match="ambiguous"):
        parsed(registry=registry)


def test_unparsed_identifier_row_cannot_hide_ambiguity():
    with pytest.raises(ValueError, match="ambiguous"):
        parsed(registry=SUMMARY.replace("AMFI Codes", "unrecognized mapping row\nAMFI Codes"))


def test_contract_is_separate_additive_and_dormant():
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    old = create_enrichment_app(dsn).openapi()
    new = create_official_facts_app(dsn).openapi()
    assert len(new["paths"]) == 18 and len(old["paths"]) == 15
    for route in old["paths"]:
        assert new["paths"][route] == old["paths"][route]
    for key, schema in old["components"]["schemas"].items():
        assert new["components"]["schemas"][key] == schema
    with TestClient(create_official_facts_app(dsn)) as client:
        assert client.get("/api/enrichment/v2/policy").status_code == 503
        assert client.post("/api/enrichment/v2/funds/1/facts", json={}).status_code == 405


def prepare(dsn, monkeypatch):
    with connection(dsn) as conn:
        conn.execute("UPDATE mf.amcs SET amfi_amc_name=%s WHERE amc_id=1", (facts.AMC,))
        conn.execute("UPDATE mf.funds SET scheme_name=%s,isin_growth_or_div_payout=CASE WHEN amfi_scheme_code=1 THEN %s ELSE 'INF209K01BO6' END", (NAME, ISIN))
    install_evidence(dsn)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        facts_operations.install(conn, ROOT)
    monkeypatch.setattr(facts, "decode", lambda content, page: (DETAIL if page == 15 else SUMMARY, {**LINEAGE, "page": page}))
    doc = import_document(dsn, b"%PDF-facts", "https://mutualfund.adityabirlacapital.com/-/media/facts.pdf")
    registry = import_document(dsn, b"%PDF-registry", "https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_fixture.pdf")
    return facts_schemas.Selection(document_id=doc, identity_document_id=registry, facts_page=15, identity_page=28, codes=[1, 2])


def import_document(dsn, content, url):
    from urllib.parse import urlsplit
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        record = evidence.append(conn, intake(content, media_type="application/pdf", amc=facts.AMC,
            source_url=url, final_url=url, official_hosts=[urlsplit(url).hostname], codes=[1, 2], document_as_of="2026-08-31"), content)
    return record["document"]["document_id"]


def publish(dsn, selection):
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        return facts_store.publish(conn, selection)


@pytest.mark.postgres
def test_database_install_replay_immutable_publication_and_source_preservation(verified_db, monkeypatch):
    selection = prepare(verified_db, monkeypatch)
    with connection(verified_db, readonly=True) as conn:
        before = source_state(conn)
        from mfdataindia.enrichment.operations import capture_enrichment
        retained = capture_enrichment(conn, include_rows=True)
        assert all(len(v["row_hashes"]) == v["rows"] for v in retained.values())
        assert {k:{name:value for name,value in v.items() if name != "row_hashes"} for k,v in retained.items()} == capture_enrichment(conn)
    first = publish(verified_db, selection)
    assert publish(verified_db, selection) == first
    assert len(first) == 2
    with connection(verified_db) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        assert facts_operations.install(conn, ROOT)["status"] == "ALREADY_INSTALLED"
        assert source_state(conn) == before
        assert not conn.execute("SELECT has_table_privilege('mfdata_ingest','mf_official_facts.snapshots','INSERT') AS yes").fetchone()["yes"]
        assert conn.execute("SELECT count(*) AS n FROM mf_official_facts.snapshots").fetchone()["n"] == 2
    for role in ROLES[1:]:
        with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
            facts_store.verify_acl(conn)
            result = facts_schemas.Response.model_validate(facts_store.get(conn, 1)).model_dump(mode="json")
            assert result["state"] == "available" and not result["freshness_certified"]
            assert facts_store.snapshots(conn, 1)["items"] == [first[0]["snapshot_id"]]
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("INSERT INTO mf_official_facts.snapshots SELECT * FROM mf_official_facts.snapshots")
    with connection(verified_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM mf_official_facts.snapshots")


@pytest.mark.postgres
def test_atomic_batch_rejection_stale_identity_and_forged_sql_seal(verified_db, monkeypatch):
    selection = prepare(verified_db, monkeypatch)
    original = facts.derive
    def fail_second(identity, *args, **kw):
        if identity["code"] == 2:
            raise ValueError("second mapping failed")
        return original(identity, *args, **kw)
    monkeypatch.setattr(facts, "derive", fail_second)
    with pytest.raises(ValueError):
        publish(verified_db, selection)
    with connection(verified_db, readonly=True) as conn:
        assert facts_store.capture(conn)["rows"] == 0
    monkeypatch.setattr(facts, "derive", original)
    first = publish(verified_db, selection)
    forged = deepcopy(first[0])
    forged["identity_hash"] = "0" * 64
    forged["snapshot_id"] = fingerprint({k:v for k,v in forged.items() if k != "snapshot_id"})
    with connection(verified_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="seal"):
            conn.execute("INSERT INTO mf_official_facts.snapshots VALUES(%s,1,%s,%s,%s,%s,%s)", (forged["snapshot_id"], selection.document_id, selection.identity_document_id, forged["policy_hash"], forged["disclosure_as_of"], Jsonb(forged)))
    with connection(verified_db) as conn:
        conn.execute("UPDATE mf.funds SET scheme_name='Changed' WHERE amfi_scheme_code=1")
    with connection(verified_db, readonly=True) as conn:
        assert facts_store.get(conn, 1)["state"] == "identity_changed"
        assert facts_store.get(conn, 1, snapshot_id=first[0]["snapshot_id"])["snapshot"] is None


@pytest.mark.postgres
def test_conflicting_same_date_sources_not_silently_selected(verified_db, monkeypatch):
    selection = prepare(verified_db, monkeypatch)
    first = publish(verified_db, selection)
    revised = import_document(verified_db, b"%PDF-revised", "https://mutualfund.adityabirlacapital.com/-/media/revised.pdf")
    publish(verified_db, selection.model_copy(update={"document_id": revised}))
    with connection(verified_db, readonly=True) as conn:
        current = facts_store.get(conn, 1)
        assert current["state"] == "conflicting" and current["snapshot"] is None
        assert len(current["conflicting_snapshot_ids"]) == 2
        assert facts_store.get(conn, 1, snapshot_id=first[0]["snapshot_id"])["state"] == "available"
        page = facts_store.snapshots(conn, 1, limit=1)
        assert page["next_after"]
        assert len(facts_store.snapshots(conn, 1, after=page["next_after"], limit=1)["items"]) == 1
        with pytest.raises(review.NotFound):
            facts_store.get(conn, 2, snapshot_id=first[0]["snapshot_id"])


@pytest.mark.postgres
def test_new_private_api_denials_projection_old_v1_and_no_original_overwrite(verified_db, monkeypatch):
    from test_historical_store_api import foundations, install as install_historical
    foundations(verified_db)
    install_historical(verified_db)
    selection = prepare(verified_db, monkeypatch)
    first = publish(verified_db, selection)
    key, verifier = trust()
    headers = {"Authorization": "Bearer " + assertion(key, scope=["review:read"])}
    with TestClient(create_official_facts_app(runtime_dsn(verified_db, ROLES[2]), verifier=verifier)) as client:
        for route in ("/api/enrichment/v2/policy", "/api/enrichment/v2/funds/1/facts", "/api/enrichment/v2/funds/1/snapshots"):
            assert client.get(route).status_code == 401
            result = client.get(route, headers=headers)
            assert result.status_code == 200, result.text
            assert result.headers["Cache-Control"] == "no-store"
        assert client.get('/api/enrichment/v2/funds/1/facts?snapshot_id=' + first[0]['snapshot_id'],headers=headers).status_code == 200
        assert client.get('/api/enrichment/v2/funds/1/snapshots?limit=101',headers=headers).status_code == 422
        assert client.get('/api/enrichment/v1/funds/1/coverage',headers=headers).json()['facts_available'] is False
        assert client.post('/api/enrichment/v2/funds/1/facts',headers=headers,json={}).status_code == 405
    with connection(verified_db, readonly=True) as conn:
        assert conn.execute("SELECT aum FROM mf.fund_facts WHERE amfi_scheme_code=1").fetchone()["aum"] == __import__('decimal').Decimal('123.45')


@pytest.mark.postgres
def test_certificate_rejects_false_pass_and_runtime_capability_drift(verified_db, monkeypatch):
    from mfdataindia.enrichment.facts_certification import projections
    selection = prepare(verified_db, monkeypatch)
    publish(verified_db, selection)
    oracle = {"benchmark_name": "Nifty 100 TRI", "fund_managers": ["Mr. Harish Krishnan"], "base_expense_ratio": "1.35"}
    with connection(verified_db, readonly=True) as conn:
        assert len(projections(conn, selection, oracle)) == 7
        with pytest.raises(ValueError, match="expectation"):
            projections(conn, selection, {})
        with pytest.raises(ValueError, match="expectation"):
            projections(conn, selection, {**oracle, "base_expense_ratio": "0.81"})
        with pytest.raises(ValueError, match="missing"):
            projections(conn, selection.model_copy(update={"facts_page": 16}), oracle)
    with connection(verified_db) as conn:
        conn.execute("GRANT INSERT(document) ON mf_official_facts.snapshots TO mfdata_verified_reader")
    with connection(verified_db, readonly=True) as conn:
        with pytest.raises(ValueError, match="grants"):
            facts_store.verify_acl(conn, principal=ROLES[1])
