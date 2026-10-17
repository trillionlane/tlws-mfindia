"""Synthetic safety proofs, never real source/DEV acceptance evidence."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import (
    absl_expansion,
    amc_book_pdf,
    book_acquisition,
    book_facts,
    book_projection,
    book_runtime,
    book_sources,
    book_store,
    current_facts_store as current,
    hdfc_monthly,
    multi_facts,
    multi_projection,
)
from mfdataindia.review.app import create_amc_books_app, create_multi_amc_app
from mfdataindia.review.policy import fingerprint
from test_absl_expansion import NAME, ISIN, DETAIL, CHANGES, page
from test_multi_amc_facts import setup as setup_prior, publish as publish_prior, reference
from test_verified_review_api import runtime_dsn
from test_verified_review_store import ROLES, connection, source_state
from test_verified_review_store import verified_db as verified_db

ROOT = Path(__file__).resolve().parents[1]
KEY = "absl-expanded"
NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def test_contract_is_additive_and_private_without_configured_verifier():
    from fastapi.testclient import TestClient

    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    old, new = create_multi_amc_app(dsn).openapi(), create_amc_books_app(dsn).openapi()
    assert len(new["paths"]) == 52
    assert all(new["paths"][k] == v for k, v in old["paths"].items())
    assert all(
        new["components"]["schemas"][k] == v for k, v in old["components"]["schemas"].items()
    )
    with TestClient(create_amc_books_app(dsn)) as client:
        for path in (
            "/api/enrichment/v9/policy",
            "/api/enrichment/v9/coverage",
            "/api/enrichment/v9/funds/100001/facts",
        ):
            response = client.get(path)
            assert response.status_code in (401, 503)
            assert response.headers["cache-control"] == "no-store"


def test_registry_cannot_be_overridden_and_retains_frozen_two_sources(tmp_path):
    document = book_sources.registry()
    assert document["maximum_sources_per_execution"] == 3
    assert document["sources"][0]["preserved_projection_codes"] == [101763, 103173, 103174]
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "official-book-sources-v1.json").write_text("{}")
    with pytest.raises(ValueError, match="registry differs"):
        book_sources.registry(root=tmp_path)
    with pytest.raises(ValueError, match="key required"):
        book_sources.source("caller-amc")
    with pytest.raises(ValueError):
        book_sources.registry(version=True)


def test_discovery_requires_two_distinct_nonfuture_editions_and_dates():
    landing = b"var factsheetDatasourceId = '{00000000-0000-0000-0000-000000000001}';"

    def transport(url, **kwargs):
        return {
            "content": json.dumps(
                {
                    "ReturnCode": 1,
                    "MonthlyFactsheets": [
                        {
                            "DocumentTitle": "Empower for the Month of " + month + " 2026",
                            "DocumentLink": "/-/media/bsl/files/resources/factsheets/2026/"
                            + month
                            + ".pdf",
                        }
                        for month in ("September", "October")
                    ],
                }
            ).encode()
            if kwargs
            else landing,
            "source_url": url,
            "media_type": "application/json" if kwargs else "text/html",
        }

    catalogs, editions = book_acquisition.discover_absl(NOW, transport=transport)
    assert len(catalogs) == 2 and [e["as_of"] for e in editions] == ["2026-09-30", "2026-08-31"]
    with pytest.raises(ValueError, match="future"):
        book_acquisition.discover_absl(datetime(2026, 9, 1), transport=transport)


def fixture_registry(monkeypatch):
    reg = json.loads((ROOT / "config/official-book-sources-v1.json").read_text())
    summary_raw = b"%PDF-synthetic-issuer-identity-only"
    reg["sources"][0]["identity_documents"][0]["sha256"] = hashlib.sha256(summary_raw).hexdigest()
    reg["sources"][0]["minimum_detail_pages"] = 1
    monkeypatch.setattr(book_sources, "registry", lambda version=None, root=None: deepcopy(reg))
    return summary_raw


def fixtures(identities, summary_raw):
    summary_text = f"1 Fund Name {NAME}\n28 ISINs\n{NAME} - Regular Growth {ISIN}\n29 AMFI Codes (To be phased out)\n{NAME} - Regular Growth 100001\n30 SEBI Codes fixture\n"
    summary = {
        "source_hash": hashlib.sha256(summary_raw).hexdigest(),
        "pages": [page(summary_text, 1)],
    }
    originals = [
        {
            "content": summary_raw,
            "source_url": book_sources.source(KEY)["identity_documents"][0]["url"],
            "media_type": "application/pdf",
        }
    ]
    batches = []
    for label, as_of, month in (
        ("oct", "2026-09-30", "September"),
        ("sep", "2026-08-31", "August"),
    ):
        raw = b"%PDF-synthetic-book-" + label.encode()
        url = book_sources.source(KEY)["book_prefix"] + "2026/fixture-" + label + ".pdf"
        extraction = {
            "source_hash": hashlib.sha256(raw).hexdigest(),
            "policy_hash": fingerprint(amc_book_pdf.policy()),
            "extraction_hash": "e" * 64,
            "pages": [
                page(
                    DETAIL.replace(
                        "September 30", month + (" 30" if month == "September" else " 31")
                    ),
                    1,
                ),
                page(CHANGES.replace("September", month), 2),
            ],
        }
        batches.append(
            book_facts.compile_batch(
                KEY, identities, extraction, summary, as_of=as_of, source_url=url
            )
        )
        originals.append({"content": raw, "source_url": url, "media_type": "application/pdf"})
    catalogs = [
        {
            "source_url": book_sources.source(KEY)["catalogue_landing"],
            "content": b"synthetic landing",
            "media_type": "text/html",
        },
        {
            "source_url": "https://mutualfund.adityabirlacapital.com/api/sitecore/CalculatorPage/GetMonthlyFactsheetsByYear?datasourceId=%7B00000000-0000-0000-0000-000000000001%7D&year=2026",
            "content": b'{"synthetic":"catalogue"}',
            "media_type": "application/json",
        },
    ]
    archives = [
        {
            "uri": "gs://tlws_mf_data_source/"
            + book_sources.source(KEY)["archive_prefix"]
            + hashlib.sha256(o["content"]).hexdigest()
            + ".pdf",
            "generation": "1",
            "sha256": hashlib.sha256(o["content"]).hexdigest(),
            "size": len(o["content"]),
            "md5": "fixture",
        }
        for o in originals
    ]
    return batches, [*catalogs, *originals], archives


def prepared(dsn, monkeypatch):
    originals, archives, _ = setup_prior(dsn, monkeypatch)
    publish_prior(dsn, originals, archives)
    snapshot = hdfc_monthly.derive(
        multi_facts.source()["identity"], originals["edition"], originals["identity_source"]
    )
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        multi_projection.activate(conn, ROOT, reference(snapshot))
        amc = conn.execute(
            "SELECT amc_id FROM mf.amcs WHERE amfi_amc_name=%s", (absl_expansion.AMC,)
        ).fetchone()["amc_id"]
        conn.execute(
            "INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout) VALUES(100001,%s,%s,%s,'OPEN_ENDED','Equity','REGULAR','GROWTH',%s)",
            (amc, NAME, NAME.upper(), ISIN),
        )
        conn.execute(
            "INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type) VALUES(100002,%s,'Synthetic unlabelled','SYNTHETIC UNLABELLED','OPEN_ENDED','Equity','UNLABELLED','UNKNOWN')",
            (amc,),
        )
    summary_raw = fixture_registry(monkeypatch)
    with connection(dsn) as conn:
        before = source_state(conn)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        book_store.install(conn, ROOT)
        identities = book_store.population(conn, KEY)
    return identities, fixtures(identities, summary_raw), before


def publish(dsn, identities, inputs, suffix="fixture"):
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        return book_runtime.publish(
            conn, KEY, multi_facts.JOB + "-" + suffix, NOW, identities, *inputs
        )


@pytest.mark.postgres
def test_two_period_complete_population_publication_projection_recovery_and_replay(
    verified_db, monkeypatch
):
    identities, inputs, before = prepared(verified_db, monkeypatch)
    first = publish(verified_db, identities, inputs)
    assert first["status"] == "updated"
    assert publish(verified_db, identities, inputs) == first
    assert publish(verified_db, identities, inputs, "replay")["status"] == "unchanged"
    batch = inputs[0][0]
    refs = [
        {
            "code": 100001,
            "batch_id": batch["batch_id"],
            "as_of": "2026-09-30",
            "values": {
                "benchmark_name": "Nifty 500 TRI",
                "aum": "12345.67",
                "risk_level": "Very High",
            },
        }
    ]
    with connection(verified_db) as conn:
        old = conn.execute(
            "SELECT * FROM mf_live_facts.field_versions ORDER BY code,field,as_of"
        ).fetchall()
        cfg = current.configuration(conn)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        activation = book_projection.activate(
            conn, ROOT, refs, expected_configuration_hash=fingerprint(dict(cfg))
        )
        assert current.get(conn, 100001)["fields"]["aum"]["value"] == "12345.67"
        assert (
            conn.execute(
                "SELECT * FROM mf_live_facts.field_versions WHERE code=ANY(%s) ORDER BY code,field,as_of",
                (list({r["code"] for r in old}),),
            ).fetchall()
            == old
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert (
            book_projection.recover(conn, ROOT, activation["transition_id"])["status"]
            == "RECOVERED"
        )
        assert (
            conn.execute(
                "SELECT * FROM mf_live_facts.field_versions ORDER BY code,field,as_of"
            ).fetchall()
            == old
        )
        assert (
            book_projection.recover(conn, ROOT, activation["transition_id"])["status"]
            == "ALREADY_RECOVERED"
        )
    with connection(verified_db) as conn:
        assert source_state(conn) == before
        status = book_store.status(conn)
        assert status["sources"][0]["current_denominator"] == len(identities)
        assert status["sources"][0]["population_matches_latest_success"]
        assert len(book_store.fund(conn, 100001)["observations"]) == 2
        unlabelled = book_store.fund(conn, 100002)
        assert len(unlabelled["observations"]) == 2
        assert all(
            r["result"]["reason"] == "unsupported_current_identity"
            for r in unlabelled["observations"]
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        book_projection.activate(
            conn,
            ROOT,
            refs,
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
        with pytest.raises(ValueError, match="stale book intent"):
            book_projection.recover(conn, ROOT, activation["transition_id"])


@pytest.mark.parametrize(
    "defect",
    [
        "no_fields",
        "missing_risk",
        "missing_prior",
        "one_period",
        "unknown_source",
        "wrong_seal",
        "duplicate_samples",
    ],
)
def test_certification_cannot_false_pass_incomplete_references(defect):
    from types import SimpleNamespace
    from mfdataindia.enrichment.book_certification import expectation

    m = SimpleNamespace(seal="fixture", cohort=[100001, 100002, 101763, 103173, 103174])
    aliases = {
        a for group in book_sources.current_policy()["supported_fields"].values() for a in group
    }
    value = {
        "reviewed": [
            {
                "code": c,
                "batch_id": "a" * 64,
                "as_of": "2026-09-30",
                "values": {k: "synthetic fixture" for k in absl_expansion.FIELDS},
            }
            for c in (100001, 100002)
        ],
        "prior_core_values": {str(c): {k: None for k in aliases} for c in (100001, 100002)},
        "preserved_core_values": {
            str(c): {k: None for k in aliases} for c in (101763, 103173, 103174)
        },
        "execution_id": multi_facts.JOB + "-fixture",
        "sources": {
            KEY: {
                "denominator": 192,
                "population_hash": "c" * 64,
                "batch_ids": ["a" * 64, "b" * 64],
            }
        },
    }

    def env():
        return {
            "AMC_BOOK_CERTIFICATION": json.dumps(value),
            "AMC_BOOK_CERTIFICATION_HASH": fingerprint(
                {"manifest": m.seal, "certification": value}
            ),
        }

    assert expectation(m, env()) == value
    if defect == "no_fields":
        value["reviewed"][0]["values"] = {}
    elif defect == "missing_risk":
        for r in value["reviewed"]:
            r["values"].pop("risk_level")
    elif defect == "missing_prior":
        value["preserved_core_values"]["101763"].pop("risk_level")
    elif defect == "one_period":
        value["sources"][KEY]["batch_ids"] = ["a" * 64]
    elif defect == "unknown_source":
        value["sources"]["arbitrary"] = value["sources"].pop(KEY)
    elif defect == "duplicate_samples":
        value["reviewed"][1]["code"] = 100001
    else:
        broken = env()
        broken["AMC_BOOK_CERTIFICATION_HASH"] = "0" * 64
        with pytest.raises(ValueError):
            expectation(m, broken)
        return
    with pytest.raises(ValueError):
        expectation(m, env())


@pytest.mark.postgres
@pytest.mark.parametrize(
    "defect",
    [
        "partial_population",
        "one_period",
        "bad_archive",
        "bad_unit",
        "unavailable_value",
        "wrong_dataset",
    ],
)
def test_direct_writer_cannot_publish_incomplete_or_invalid_evidence(
    verified_db, monkeypatch, defect
):
    identities, inputs, _ = prepared(verified_db, monkeypatch)
    batches, originals, archives = deepcopy(inputs)
    if defect == "partial_population":
        identities = identities[:1]
        batches = [
            {**b, "observations": b["observations"][:1], "population_hash": fingerprint(identities)}
            for b in batches
        ]
    elif defect == "one_period":
        batches = batches[:1]
    elif defect == "bad_archive":
        archives[0]["uri"] = "gs://wrong/object"
    elif defect in ("bad_unit", "unavailable_value"):
        row = next(r for r in batches[0]["observations"] if r["identity"]["code"] == 100001)
        row["result"]["fields"]["aum"].update(
            {"unit": "INR_lakh"}
            if defect == "bad_unit"
            else {"status": "unavailable", "reason": "fixture"}
        )
    for b in batches:
        b["batch_id"] = fingerprint({k: v for k, v in b.items() if k != "batch_id"})
    if defect == "wrong_dataset":
        real = book_store.append_run
        monkeypatch.setattr(
            book_store, "append_run", lambda conn, body: real(conn, {**body, "dataset_version": -1})
        )
    with pytest.raises((ValueError, psycopg.errors.RaiseException)):
        publish(verified_db, identities, (batches, originals, archives))
    with connection(verified_db) as conn:
        assert conn.execute("SELECT count(*) AS n FROM mf_book_facts.runs").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM mf_book_facts.batches").fetchone()["n"] == 0


@pytest.mark.postgres
def test_production_default_grants_raw_and_source_write_boundaries(verified_db, monkeypatch):
    prepared(verified_db, monkeypatch)
    with connection(verified_db) as conn:
        for table in book_store.TABLES | {"prior_field_versions"}:
            assert not conn.execute(
                "SELECT has_table_privilege('mfdata_ingest',%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE') AS allowed",
                ("mf_book_facts." + table,),
            ).fetchone()["allowed"]
        for role in ROLES:
            book_store.verify_acl(conn, principal=role)
    for role in ROLES:
        with psycopg.connect(runtime_dsn(verified_db, role)) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "UPDATE mf.funds SET scheme_name='FORBIDDEN' WHERE amfi_scheme_code=100001"
                )
        if role != ROLES[0]:
            with psycopg.connect(runtime_dsn(verified_db, role)) as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute("SELECT content FROM mf_book_facts.sources")


@pytest.mark.postgres
def test_database_itself_rejects_partial_population_and_staged_batches_do_not_publish(
    verified_db, monkeypatch
):
    identities, inputs, _ = prepared(verified_db, monkeypatch)
    first = publish(verified_db, identities, inputs)
    subset = identities[-1:]
    batches = deepcopy(inputs[0])
    for b in batches:
        b["observations"] = [r for r in b["observations"] if r["identity"] in subset]
        b["population_hash"] = fingerprint(subset)
        b["batch_id"] = fingerprint({k: v for k, v in b.items() if k != "batch_id"})
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        for b in batches:
            book_store.append_exact(
                conn,
                "batches",
                "batch_id",
                b,
                {
                    k: b[k]
                    for k in (
                        "source_key",
                        "content_hash",
                        "identity_content_hash",
                        "disclosure_as_of",
                    )
                },
            )
    with connection(verified_db) as conn:
        assert len(book_store.fund(conn, subset[0]["code"])["observations"]) == 2
    body = {k: v for k, v in first.items() if k != "run_id"}
    body.update(
        execution_id=multi_facts.JOB + "-direct-partial",
        status="updated",
        batch_ids=[b["batch_id"] for b in batches],
        population_hash=fingerprint(subset),
    )
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="complete current"):
            book_store.append_run(conn, body)


@pytest.mark.postgres
def test_later_unavailable_and_equal_date_conflict_retain_previous_and_recovery_ignores_source_checks(
    verified_db, monkeypatch
):
    identities, inputs, _ = prepared(verified_db, monkeypatch)
    publish(verified_db, identities, inputs)
    batch = inputs[0][0]
    refs = [
        {
            "code": 100001,
            "batch_id": batch["batch_id"],
            "as_of": "2026-09-30",
            "values": {"aum": "12345.67"},
        }
    ]
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        activation = book_projection.activate(
            conn,
            ROOT,
            refs,
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
    # Append, never update, a disagreeing same-date original and complete receipt.
    batches, originals, archives = deepcopy(inputs)
    raw = b"%PDF-synthetic-corrected-edition-conflict"
    old_hash = batches[0]["content_hash"]
    new_hash = hashlib.sha256(raw).hexdigest()
    for original in originals:
        if hashlib.sha256(original["content"]).hexdigest() == old_hash:
            original["content"] = raw
    for a in archives:
        if a["sha256"] == old_hash:
            a.update(sha256=new_hash, size=len(raw), uri=a["uri"].replace(old_hash, new_hash))
    batches[0]["content_hash"] = new_hash
    row = next(r for r in batches[0]["observations"] if r["identity"]["code"] == 100001)
    row["result"]["fields"]["aum"].update(value="54321.00", raw_value="54321.00 Cr")
    batches[0]["batch_id"] = fingerprint({k: v for k, v in batches[0].items() if k != "batch_id"})
    publish(verified_db, identities, (batches, originals, archives), "conflict")
    with connection(verified_db) as conn:
        field = current.get(conn, 100001)["fields"]["aum"]
        assert field["state"] == "conflicting" and field["value"] == "12345.67"
        assert field["disclosure_as_of"] == "2026-08-31"
    monkeypatch.setattr(
        book_store,
        "verify_acl",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("candidate broken")),
    )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert (
            book_projection.recover(conn, ROOT, activation["transition_id"])["status"]
            == "RECOVERED"
        )
