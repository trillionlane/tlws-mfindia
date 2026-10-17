"""Disposable PostgreSQL proofs. All originals here are labelled synthetic."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import (
    amfi_summary_identity,
    book_preservation,
    book_projection,
    book_runtime,
    book_sources,
    book_store,
    book_upgrade,
    current_facts_store as current,
    kotak_acquisition,
    kotak_batch,
    kotak_book,
    layout_book_pdf,
)
from mfdataindia.review.policy import fingerprint
from test_amc_book_facts import ROOT
from test_kotak_book import text
from test_sbi_store import accepted_fs08, source_inputs
from test_verified_review_api import runtime_dsn
from test_verified_review_store import ROLES, source_state
from test_verified_review_store import verified_db as verified_db

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)
CURRENT = "https://vatseelabs-s3.kotakmf.com/FormsDownloads/Factsheet/Factsheet-for-September-2026/KotakMFFactsheetSeptember2026.pdf"
RAW_CURRENT = b"%PDF-SYNTHETIC-KOTAK-CURRENT-NOT-ISSUER"
RAW_OLD = b"%PDF-SYNTHETIC-KOTAK-JULY-NOT-ISSUER"


def accepted_sbi(dsn, monkeypatch):
    registries = book_sources.REGISTRIES
    monkeypatch.setattr(book_sources, "REGISTRIES", {k: v for k, v in registries.items() if k <= 2})
    accepted_fs08(dsn, monkeypatch)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        book_upgrade.install(conn, ROOT)
        identities = book_store.population(conn, "sbi-expanded")
    batches, originals, archives = source_inputs(identities)
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        book_runtime.publish(
            conn,
            "sbi-expanded",
            "tlws-mf-official-facts-refresh-dev-synthetic-sbi-prior",
            NOW,
            identities,
            batches,
            originals,
            archives,
            availability=[],
        )
    expected = {
        "benchmark_name": "BSE 500 (TRI)",
        "fund_managers": ["Mr. Anup Upadhyay"],
        "base_expense_ratio": "1.4",
        "aum": "22109.87",
        "risk_level": "Very High",
    }
    refs = [
        {
            "code": i["code"],
            "batch_id": batches[0]["batch_id"],
            "as_of": "2026-09-30",
            "values": expected,
            "source_values": {**expected, "base_expense_ratio": "1.40"},
        }
        for i in identities
    ]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        book_projection.activate(
            conn,
            ROOT,
            refs,
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
        conn.execute(
            "INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES(%s,%s)",
            (kotak_book.AMC, kotak_book.AMC.upper()),
        )
        amc = conn.execute(
            "SELECT amc_id FROM mf.amcs WHERE amfi_amc_name=%s", (kotak_book.AMC,)
        ).fetchone()["amc_id"]
        for code, plan, option, isin in [
            (112090, "REGULAR", "GROWTH", "INF174K01336"),
            (112091, "REGULAR", "IDCW", "INF174K01351"),
            (112092, "UNLABELLED", "UNKNOWN", None),
        ]:
            conn.execute(
                "INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout) VALUES(%s,%s,%s,%s,'OPEN_ENDED','Equity',%s,%s,%s)",
                (
                    code,
                    amc,
                    "Kotak Flexicap Fund - Regular Plan - " + option,
                    "SYNTHETIC KOTAK " + option,
                    plan,
                    option,
                    isin,
                ),
            )
    reg1, reg2 = book_sources.registry(1), book_sources.registry(2)
    reg3 = json.loads((ROOT / "config/official-book-sources-v3.json").read_text())
    reg3["sources"][:2] = deepcopy(reg2["sources"])
    reg3["sources"][2]["minimum_current_detail_pages"] = 1
    reg3["sources"][2]["reviewed_historical"]["sha256"] = hashlib.sha256(RAW_OLD).hexdigest()
    versions = {1: reg1, 2: reg2, 3: reg3}
    monkeypatch.setattr(book_sources, "REGISTRIES", registries)
    monkeypatch.setattr(book_sources, "CURRENT_VERSION", 3)
    monkeypatch.setattr(
        book_sources,
        "registry",
        lambda version=None, root=None: deepcopy(
            versions[book_sources.CURRENT_VERSION if version is None else version]
        ),
    )


def fixtures(identities):
    catalogue = {
        "source_url": kotak_acquisition.recent_catalogues(NOW)[0]["source_url"],
        "media_type": "application/json",
        "role": "catalogue",
        "content": json.dumps(
            {
                "status": "Success",
                "statusCode": "200",
                "subHeaderList": [
                    {
                        "headerId": 219,
                        "contentType": "upload",
                        "content": CURRENT.removeprefix("https://vatseelabs-s3.kotakmf.com/"),
                    }
                ],
            }
        ).encode(),
    }
    directory = {
        "source_url": kotak_book.DIRECTORY,
        "media_type": "application/json",
        "role": "directory",
        "content": b'[{"scheme_id":"4372","scheme_name":"Kotak Flexicap Fund"}]',
    }
    url = "https://portal.amfiindia.com/spages/SSD_4372.xml"
    index = {
        "source_url": "https://www.amfiindia.com/api/schemes/4372/documents",
        "media_type": "application/json",
        "role": "index",
        "content": json.dumps({"data": [{"schemeId": "4372", "summaryXmlUrl": url}]}).encode(),
    }
    xml = b"<SchemeSummaryDocument><SchemeSummary><Fund_Name>Kotak Flexicap Fund</Fund_Name><AMFI_Codes_To_be_phased_out>Regular Plan-Growth-112090 Regular Plan-IDCW-112091</AMFI_Codes_To_be_phased_out><ISINs>Regular Plan-Growth-INF174K01336 Regular Plan-IDCW Payout-INF174K01351</ISINs></SchemeSummary></SchemeSummaryDocument>"
    originals = [
        catalogue,
        directory,
        index,
        {"source_url": url, "media_type": "application/xml", "role": "identity", "content": xml},
        {
            "source_url": CURRENT,
            "media_type": "application/pdf",
            "role": "book",
            "content": RAW_CURRENT,
        },
        {
            "source_url": kotak_book.HISTORICAL,
            "media_type": "application/pdf",
            "role": "book",
            "content": RAW_OLD,
        },
    ]
    bridges = amfi_summary_identity.rows(xml, url, hashlib.sha256(index["content"]).hexdigest())
    engine = book_sources.specification(kotak_book.KEY)["decoder_engine_hash"]
    batches = []
    for as_of, raw, url in [
        ("2026-09-30", RAW_CURRENT, CURRENT),
        ("2026-07-31", RAW_OLD, kotak_book.HISTORICAL),
    ]:
        value = (
            text()
            if as_of == "2026-09-30"
            else text()
            .replace("30th September, 2026", "31st July, 2026")
            .replace("53,086.64", "50,000.00")
        )
        body = {
            "source_hash": hashlib.sha256(raw).hexdigest(),
            "policy_hash": fingerprint(layout_book_pdf.policy("kotak")),
            "engine_hash": engine,
            "pages": [
                {"page": 1, "text": value, "text_hash": hashlib.sha256(value.encode()).hexdigest()}
            ],
            "document_pages": 1,
        }
        extraction = {**body, "extraction_hash": fingerprint(body)}
        batches.append(
            kotak_batch.compile_batch(
                identities,
                {"source_url": url, "extraction": extraction},
                bridges,
                originals,
                as_of=as_of,
                directory_hash=hashlib.sha256(directory["content"]).hexdigest(),
                expected_periods=["2026-09-30", "2026-08-31"],
            )
        )
    archives = [
        {
            "uri": "gs://tlws_mf_data_source/official-monthly-facts/kotak-expanded/"
            + hashlib.sha256(o["content"]).hexdigest()
            + (".pdf" if o["media_type"] == "application/pdf" else ".xml"),
            "sha256": hashlib.sha256(o["content"]).hexdigest(),
            "size": len(o["content"]),
            "generation": "1",
            "md5": "SYNTHETIC-CONTRACT-NOT-LIVE",
        }
        for o in originals
        if o["media_type"] in ("application/pdf", "application/xml")
    ]
    return batches, originals, archives


def prepared(dsn, monkeypatch):
    accepted_sbi(dsn, monkeypatch)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        prior = dict(current.configuration(conn))
        preserved = book_preservation.capture(conn, 2)
        original = source_state(conn)
        book_upgrade.install(conn, ROOT)
        identities = book_store.population(conn, kotak_book.KEY)
    return identities, fixtures(identities), prior, preserved, original


def publish(dsn, identities, inputs, *, execution="synthetic-kotak", gaps=True):
    batches, originals, archives = inputs
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        return book_runtime.publish(
            conn,
            kotak_book.KEY,
            "tlws-mf-official-facts-refresh-dev-" + execution,
            NOW,
            identities,
            batches,
            originals,
            archives,
            availability=[
                {"expected_period": "2026-08-31", "reason": "expected_recent_period_not_acquired"}
            ]
            if gaps
            else [],
        )


def references(batch):
    result = []
    for code in (112090, 112091):
        row = next(o for o in batch["observations"] if o["identity"]["code"] == code)
        values = {key: value["value"] for key, value in row["result"]["fields"].items()}
        result.append(
            {
                "code": code,
                "batch_id": batch["batch_id"],
                "as_of": batch["disclosure_as_of"],
                "values": values,
                "source_values": values,
            }
        )
    return result


@pytest.mark.postgres
def test_complete_population_publication_honest_dates_recovery_and_original_preservation(
    verified_db, monkeypatch
):
    identities, inputs, prior, preserved, original = prepared(verified_db, monkeypatch)
    receipt = publish(verified_db, identities, inputs)
    assert receipt["status"] == "updated" and len(identities) == 3
    assert publish(verified_db, identities, inputs)["run_id"] == receipt["run_id"]
    assert [c["denominator"] for c in receipt["coverage"]] == [3, 3]
    assert [c["disclosure_as_of"] for c in receipt["coverage"]] == ["2026-09-30", "2026-07-31"]
    assert receipt["coverage"][1]["single_fund_historical_disclosure"] is True
    assert receipt["coverage"][1]["expected_recent_reporting_periods"] == [
        "2026-09-30",
        "2026-08-31",
    ]
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        activated = book_projection.activate(
            conn, ROOT, references(inputs[0][0]), expected_configuration_hash=fingerprint(prior)
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        assert book_preservation.capture(conn, 2) == preserved
        assert source_state(conn) == original
        assert current.get(conn, 112090)["fields"]["aum"]["value"] == "53086.64"
        assert current.get(conn, 112092)["fields"]["aum"]["state"] != "available"
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        book_projection.recover(conn, ROOT, activated["transition_id"])
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert book_store.installed_configuration(conn)["document"]["registry"]["version"] == 2
        assert book_preservation.capture(conn, 2) == preserved
        assert source_state(conn) == original
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert (
            book_projection.recover(conn, ROOT, activated["transition_id"])["status"]
            == "ALREADY_RECOVERED"
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        recovered = dict(current.configuration(conn))
        book_upgrade.install(conn, ROOT)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        book_projection.activate(
            conn, ROOT, references(inputs[0][0]), expected_configuration_hash=fingerprint(recovered)
        )
    assert (
        publish(verified_db, identities, inputs, execution="synthetic-kotak-next")["status"]
        == "unchanged"
    )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        assert book_preservation.capture(conn, 2) == preserved
        assert source_state(conn) == original


@pytest.mark.postgres
def test_missing_august_gap_cannot_be_omitted_from_success_receipt(verified_db, monkeypatch):
    identities, inputs, _, _, _ = prepared(verified_db, monkeypatch)
    with pytest.raises(psycopg.Error, match="missing expected month"):
        publish(verified_db, identities, inputs, gaps=False)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM mf_book_facts.runs WHERE source_key='kotak-expanded'"
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM mf_book_facts.batches WHERE source_key='kotak-expanded'"
            ).fetchone()["n"]
            == 0
        )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "defect",
    [
        "historical_august",
        "contradictory_claim",
        "cross_frequency",
        "contaminated_benchmark",
        "wrong_decoder",
        "orphan_xml",
        "unregistered_url",
    ],
)
def test_database_rejects_unbound_dates_identities_sources_and_engine(
    verified_db, monkeypatch, defect
):
    identities, inputs, _, _, _ = prepared(verified_db, monkeypatch)
    batches, originals, archives = deepcopy(inputs)
    if defect == "orphan_xml":
        originals = [o for o in originals if o["role"] != "index"]
    elif defect == "unregistered_url":
        originals[-1]["source_url"] = "https://attacker.invalid/history.pdf"
    else:
        target = batches[1] if defect == "historical_august" else batches[0]
        if defect == "historical_august":
            target["disclosure_as_of"] = "2026-08-31"
        elif defect == "contradictory_claim":
            target["observations"][0]["result"]["identity_evidence"][0]["code_claims"][0][
                "variant"
            ] = ["DIRECT", "GROWTH"]
        elif defect == "cross_frequency":
            target["observations"][0]["result"]["identity_evidence"][0]["isin_frequency"] = "daily"
        elif defect == "contaminated_benchmark":
            target["observations"][0]["result"]["fields"]["benchmark_name"]["value"] = (
                "Nifty Midcap 50 IndusInd Bank Ltd. TRI"
            )
        else:
            target["monthly_extractions"][0]["decoder_engine_hash"] = "0" * 64
        target["batch_id"] = fingerprint(
            {key: value for key, value in target.items() if key != "batch_id"}
        )
    with pytest.raises(psycopg.Error):
        publish(verified_db, identities, (batches, originals, archives))


@pytest.mark.postgres
@pytest.mark.parametrize("role", ROLES)
def test_runtime_roles_remain_restricted_after_upgrade(verified_db, monkeypatch, role):
    prepared(verified_db, monkeypatch)
    with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
        book_store.verify_acl(conn)
        assert not conn.execute(
            "SELECT has_table_privilege(current_user,'mf.funds','UPDATE') AS allowed"
        ).fetchone()["allowed"]
        assert not conn.execute(
            "SELECT has_table_privilege('mfdata_ingest','mf_book_facts.runs','INSERT') AS allowed"
        ).fetchone()["allowed"]


def test_new_migration_preserves_the_entire_prior_executable_body():
    old = book_store.migration_source(version=2).split(
        "    -- Registry v1 interpretation below remains byte-for-byte frozen.", 1
    )[1]
    new = book_store.migration_source(version=3).split(
        "    -- Registry v1 interpretation below remains byte-for-byte frozen.", 1
    )[1]
    assert new == old
    assert book_projection.PROJECTIONS[3] == book_projection.PROJECTIONS[2]
    assert (
        fingerprint(book_sources.configuration(2))
        == "fa113f5e095fe5b7ad1e738401552d8ffbea15d44ebeb5e3fdf313680f6cf221"
    )
