"""Synthetic layout cases; real original-source acceptance is a separate gate."""

from copy import deepcopy

import pytest

from mfdataindia.enrichment import monthly_facts as engine, monthly_pdf, pdf_evidence
from test_official_facts import DETAIL, SUMMARY, LINEAGE, identity
from test_verified_review_store import verified_db as verified_db

CHANGES = """Aditya Birla Sun Life Large Cap Fund
August 2026
Changes in Risk-o-Meter of scheme
Change in Previous Month Current Month
Scheme Riskometer High Very High
Changes in Expense Ratio of the scheme
Change in Previous Month Current Month
Regular 1.30% 1.35%
"""


def parsed(**kwargs):
    args = {
        "identity": identity(code=103174),
        "detail": DETAIL,
        "changes": CHANGES,
        "registry": SUMMARY.replace("-Growth 1\n", "-Growth 103174\n").replace(
            "-Regular - IDCW 2\n", "-Regular - IDCW 103173\n"
        ),
        "as_of": "2026-08-31",
        "detail_lineage": LINEAGE,
        "changes_lineage": {**LINEAGE, "page": 16},
        "identity_lineage": {**LINEAGE, "page": 28},
    }
    return engine.parse(**{**args, **kwargs})


def test_current_scheme_risk_not_previous_legend_or_benchmark_and_no_human_verification():
    value = parsed()
    assert value == parsed()
    assert value["fields"]["risk_level"]["value"] == "Very High"
    assert "Current Month" in value["fields"]["risk_level"]["source_anchor"]
    assert value["verification_status"] == "needs_verification"
    assert not value["canonical_analytics_replaced"]
    assert value["fields"]["total_expense_ratio"]["value"] is None
    assert value["fields"]["aum"]["reason"] == "unit_not_explicit"


@pytest.mark.parametrize(
    "changes",
    [
        CHANGES.replace("Scheme Riskometer High Very High", "Benchmark Riskometer High Very High"),
        CHANGES.replace(
            "Scheme Riskometer High Very High", "Riskometer Low Moderate High Very High"
        ),
        CHANGES + "Scheme Riskometer High Low\n",
        CHANGES.replace("Changes in Risk-o-Meter of scheme", "Changes in Benchmark Risk"),
    ],
)
def test_missing_ambiguous_or_wrong_risk_anchor_is_explicitly_unavailable(changes):
    result = parsed(changes=changes)["fields"]["risk_level"]
    assert result["status"] == "unavailable" and result["value"] is None


def test_explicit_aum_unit_date_and_original_token_required():
    result = parsed(detail=DETAIL.replace("29,710.82\n", "29,710.82 Cr\n"))
    field = result["fields"]["aum"]
    assert field["value"] == "29710.82" and field["unit"] == "INR_crore"
    assert field["raw_value"] == "29,710.82 Cr"
    assert field["field_effective_date"] == "2026-08-31"
    other_date = DETAIL.replace("AUM as on August 31", "AUM as on July 31").replace(
        "29,710.82\n", "29,710.82 Cr\n"
    )
    assert parsed(detail=other_date)["fields"]["aum"]["reason"] == "field_date_differs"


def test_disagreeing_current_month_ber_never_enters_accepted_current_values():
    value = parsed(changes=CHANGES.replace("1.30% 1.35%", "1.30% 1.36%"))
    assert value["fields"]["base_expense_ratio"]["value"] is None
    assert (
        value["fields"]["base_expense_ratio"]["reason"] == "detail_and_current_month_BER_disagree"
    )


@pytest.mark.parametrize(
    "update",
    [
        {"identity": identity(code=99)},
        {"identity": identity(code=103174, plan="DIRECT")},
        {"identity": identity(code=103174, scheme_name="Other Large Cap Fund")},
        {"changes": CHANGES.replace("August 2026", "September 2026")},
        {"changes": CHANGES.replace(engine.NAME, "Other Fund")},
        {"as_of": "2026-09-30"},
    ],
)
def test_exact_identity_and_two_page_date_required(update):
    with pytest.raises(ValueError):
        parsed(**update)


def test_monthly_pdf_policy_additive_and_limits_fail_before_worker():
    assert monthly_pdf.policy()["maximum_source_bytes"] == 16 * 1024 * 1024
    assert pdf_evidence.policy()["maximum_source_bytes"] == 8 * 1024 * 1024
    p = deepcopy(monthly_pdf.policy())
    p["maximum_pages"] = 1
    assert monthly_pdf.policy()["maximum_pages"] == 400
    for start, end in ((0, 1), (1, 25), (4, 3), (True, 2), (399, 401)):
        with pytest.raises(ValueError):
            monthly_pdf.extract(b"%PDF-synthetic", start=start, end=end)


def test_no_currency_percent_or_locale_guessing():
    assert engine.english_date("January 07,2026") == "2026-01-07"
    assert engine.decimal("27,932.68") == "27932.68"
    for token in ("1,23.4", "NaN", "Infinity", "1e3", "₹ 123", "1.2%"):
        with pytest.raises(ValueError):
            engine.decimal(token)
    with pytest.raises(ValueError):
        engine.english_date("Janvier 07,2026")


def test_monthly_profile_is_additive_private_dormant_and_does_not_accept_user_writes():
    from fastapi.testclient import TestClient
    from mfdataindia.review.app import create_current_facts_app, create_monthly_facts_app

    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    old = create_current_facts_app(dsn).openapi()
    app = create_monthly_facts_app(dsn)
    new = app.openapi()
    assert len(old["paths"]) == 35 and len(new["paths"]) == 37
    for path, document in old["paths"].items():
        assert new["paths"][path] == document
    for key, document in old["components"]["schemas"].items():
        assert new["components"]["schemas"][key] == document
    with TestClient(app) as client:
        assert client.get("/api/enrichment/v4/policy").status_code == 503
        assert client.post("/api/enrichment/v4/funds/103174/facts", json={}).status_code == 405


def prepare_monthly(dsn, monkeypatch):
    from pathlib import Path
    import psycopg
    from psycopg.rows import dict_row
    from mfdataindia.enrichment import monthly_store, monthly_schemas, store as evidence
    from test_official_facts import prepare
    from test_official_enrichment_evidence import intake
    from test_verified_review_api import runtime_dsn
    from test_verified_review_store import ROLES, connection

    prepare(dsn, monkeypatch)
    registry = SUMMARY.replace("-Growth 1\n", "-Growth 103174\n").replace(
        "-Regular - IDCW 2\n", "-Regular - IDCW 103173\n"
    )
    with connection(dsn) as conn:
        for code, option, isin in (
            (103174, "GROWTH", "INF209K01BR9"),
            (103173, "IDCW", "INF209K01BO6"),
        ):
            conn.execute(
                "INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout) VALUES(%s,1,%s,%s,'OPEN_ENDED','Equity','REGULAR',%s,%s)",
                (code, engine.NAME, engine.NAME.upper(), option, isin),
            )
    raw = b"%PDF-synthetic-monthly-book"
    reg = b"%PDF-synthetic-monthly-identity"
    url = "https://mutualfund.adityabirlacapital.com/-/media/bsl/files/resources/factsheets/2026/monthly.pdf"
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        doc = evidence.append(
            conn,
            intake(
                reg,
                media_type="application/pdf",
                amc=engine.facts.AMC,
                source_url="https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_monthly.pdf",
                final_url="https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_monthly.pdf",
                official_hosts=["nsearchives.nseindia.com"],
                codes=[103173, 103174],
            ),
            reg,
        )["document"]["document_id"]
    selection = monthly_schemas.Selection(
        intake=intake(
            raw,
            media_type="application/pdf",
            amc=engine.facts.AMC,
            source_url=url,
            final_url=url,
            official_hosts=["mutualfund.adityabirlacapital.com"],
            codes=[103173, 103174],
            document_as_of="2026-08-31",
        ),
        identity_document_id=doc,
        detail_page=15,
        changes_page=16,
        identity_page=28,
    )
    monkeypatch.setattr(engine.facts, "decode", lambda *args: (registry, {**LINEAGE, "page": 28}))
    monkeypatch.setattr(
        monthly_pdf,
        "extract",
        lambda *args, **kwargs: {
            "source_hash": selection.intake.expected_sha256,
            "policy_hash": "4" * 64,
            "extractor_version": "synthetic",
            "engine_hash": "5" * 64,
            "pages": [
                {"page": 15, "text": DETAIL, "text_hash": "6" * 64},
                {"page": 16, "text": CHANGES, "text_hash": "7" * 64},
            ],
        },
    )
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        assert (
            monthly_store.install(conn, Path(__file__).resolve().parents[1])["status"]
            == "INSTALLED"
        )
    return selection, raw


@pytest.mark.postgres
def test_monthly_append_replay_originals_and_effective_restrictions(verified_db, monkeypatch):
    import psycopg
    from psycopg.rows import dict_row
    from mfdataindia.enrichment import monthly_store, monthly_schemas
    from test_verified_review_api import runtime_dsn
    from test_verified_review_store import ROLES, connection, source_state

    selection, raw = prepare_monthly(verified_db, monkeypatch)
    with connection(verified_db, readonly=True) as conn:
        before = source_state(conn)
    results = []
    for _ in range(2):
        with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
            results.append(monthly_store.publish(conn, selection, raw))
    assert results[0] == results[1] and len(results[0]) == 2
    from fastapi.testclient import TestClient
    from mfdataindia.review.app import create_review_app
    from test_verified_review_auth import trust, assertion

    private, verifier = trust(frozenset({"review:read"}))
    app = create_review_app(
        runtime_dsn(verified_db, ROLES[1]), verifier=verifier, monthly_facts=True
    )
    with TestClient(app) as client:
        route = "/api/enrichment/v4/funds/103174/facts"
        assert client.get(route).status_code == 401
        headers = {"Authorization": "Bearer " + assertion(private, scope=["review:read"])}
        response = client.get(route, headers=headers)
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        assert response.json()["snapshot"]["fields"]["risk_level"]["value"] == "Very High"
        assert client.get(route, headers=headers).json() == response.json()
        assert client.post(route, json={}, headers=headers).status_code == 405
    for role in ROLES[1:]:
        with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
            monthly_store.verify_acl(conn)
            assert monthly_store.capture(conn)["original_byte_integrity_checked"] is False
            value = monthly_schemas.MonthlyResponse.model_validate(monthly_store.get(conn, 103174))
            assert (
                value.state == "available"
                and value.snapshot.fields["risk_level"].value == "Very High"
            )
            assert not value.current_core_projection_includes_monthly_snapshot
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT content FROM mf_monthly_facts.sources")
    with connection(verified_db, readonly=True) as conn:
        assert source_state(conn) == before
        assert monthly_store.capture(conn)["sources"]["rows"] == 1
        assert monthly_store.capture(conn)["snapshots"]["rows"] == 2
        for table in ("sources", "snapshots"):
            assert not conn.execute(
                "SELECT has_table_privilege('mfdata_ingest',%s,'SELECT,INSERT,UPDATE,DELETE') AS allowed",
                ("mf_monthly_facts." + table,),
            ).fetchone()["allowed"]
    for table in ("sources", "snapshots"):
        with connection(verified_db) as conn:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                conn.execute("DELETE FROM mf_monthly_facts." + table)


@pytest.mark.postgres
def test_failed_second_variant_rolls_back_entire_book_and_forged_snapshot_is_rejected(
    verified_db, monkeypatch
):
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from mfdataindia.enrichment import monthly_store
    from mfdataindia.review.policy import fingerprint
    from test_verified_review_api import runtime_dsn
    from test_verified_review_store import ROLES, connection

    selection, raw = prepare_monthly(verified_db, monkeypatch)
    original = engine.parse

    def fail_second(identity, *args, **kwargs):
        if identity["code"] == 103174:
            raise ValueError("second variant rejected")
        return original(identity, *args, **kwargs)

    monkeypatch.setattr(engine, "parse", fail_second)
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        with pytest.raises(ValueError, match="second variant"):
            monthly_store.publish(conn, selection, raw)
    with connection(verified_db, readonly=True) as conn:
        assert monthly_store.capture(conn)["sources"]["rows"] == 0
        assert monthly_store.capture(conn)["snapshots"]["rows"] == 0
    monkeypatch.setattr(engine, "parse", original)
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        result = monthly_store.publish(conn, selection, raw)
    forged = deepcopy(result[0])
    forged["identity_hash"] = "0" * 64
    forged["snapshot_id"] = fingerprint({k: v for k, v in forged.items() if k != "snapshot_id"})
    with connection(verified_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="seal differs"):
            conn.execute(
                "INSERT INTO mf_monthly_facts.snapshots VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    forged["snapshot_id"],
                    forged["code"],
                    forged["content_hash"],
                    forged["identity_document_id"],
                    forged["policy_hash"],
                    forged["disclosure_as_of"],
                    forged["identity_hash"],
                    Jsonb(forged),
                ),
            )


@pytest.mark.parametrize(
    "change", ["none", "missing_unit", "one_period", "missing_variant", "missing_risk", "bad_hash"]
)
def test_certification_requires_both_variants_two_periods_and_explicit_units(change):
    import json
    from types import SimpleNamespace
    from mfdataindia.enrichment.monthly_deployment import references
    from mfdataindia.review.policy import fingerprint

    m = SimpleNamespace(seal="a" * 64, cohort=[103174, 103173])
    fields = {
        key: {
            "value": value,
            "unit": unit,
            "status": "accepted" if value is not None else "unavailable",
        }
        for key, value, unit in [
            ("risk_level", "Very High", "risk_category"),
            ("benchmark_name", "Nifty 100 TRI", "name"),
            ("fund_managers", ["Manager"], "names"),
            ("base_expense_ratio", "1.35", "percent"),
            ("aum", None, None),
        ]
    }
    body = [
        {
            "code": code,
            "as_of": as_of,
            "snapshot_id": fingerprint([code, as_of]),
            "fields": deepcopy(fields),
        }
        for code in m.cohort
        for as_of in ("2026-08-31", "2026-09-30")
    ]
    if change == "missing_unit":
        body[0]["fields"]["aum"].pop("unit")
    if change == "one_period":
        body[0]["as_of"] = "2026-09-30"
    if change == "missing_variant":
        body[0]["code"] = body[1]["code"] = 103173
    if change == "missing_risk":
        body[0]["fields"].pop("risk_level")
    env = {
        "MONTHLY_FACTS_REFERENCE": json.dumps(body),
        "MONTHLY_FACTS_REFERENCE_HASH": fingerprint({"manifest": m.seal, "references": body}),
    }
    if change == "bad_hash":
        env["MONTHLY_FACTS_REFERENCE_HASH"] = "0" * 64
    if change == "none":
        assert references(m, env) == body
    else:
        with pytest.raises(ValueError):
            references(m, env)
