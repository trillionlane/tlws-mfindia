"""Synthetic-only PostgreSQL publication and exact prior-layer preservation."""

from copy import deepcopy
import hashlib
import json

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import (
    amfi_summary_identity,
    book_facts,
    book_preservation,
    book_projection,
    book_runtime,
    book_sources,
    book_store,
    book_upgrade,
    current_facts_store as current,
    uti_acquisition,
    uti_batch,
    uti_book,
    uti_book_pdf,
)
from mfdataindia.review.policy import fingerprint
from test_amc_book_facts import ROOT
import test_kotak_store as kotak
from test_uti_acquisition import rows as catalogue_rows
from test_uti_book import extraction as layout
from test_verified_review_api import runtime_dsn
from test_verified_review_store import ROLES, source_state
from test_verified_review_store import verified_db as verified_db

NOW = kotak.NOW


def accepted_kotak(dsn, monkeypatch):
    registries = book_sources.REGISTRIES
    identities, inputs, *_ = kotak.prepared(dsn, monkeypatch)
    kotak.publish(dsn, identities, inputs)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        book_projection.activate(
            conn,
            ROOT,
            kotak.references(inputs[0][0]),
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
        conn.execute(
            "INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES('UTI Mutual Fund','UTI MUTUAL FUND')"
        )
        amc = conn.execute(
            "SELECT amc_id FROM mf.amcs WHERE amfi_amc_name='UTI Mutual Fund'"
        ).fetchone()["amc_id"]
        for code, name, option, isin in [
            (100669, "UTI Flexi Cap Fund", "GROWTH", "INF789F01513"),
            (100668, "UTI Flexi Cap Fund", "IDCW", "INF189A01053"),
            (100822, "UTI Nifty 50 Index Fund", "GROWTH", "INF789F01JN2"),
            (100823, "UTI Nifty 50 Index Fund", "IDCW", "INF789F01JL6"),
            (100999, "UTI Unknown Synthetic Fund", "UNKNOWN", None),
        ]:
            conn.execute(
                "INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout) VALUES(%s,%s,%s,%s,'OPEN_ENDED','Equity','REGULAR',%s,%s)",
                (code, amc, name + " - Regular Plan - " + option, name.upper(), option, isin),
            )
    versions = {v: book_sources.registry(v) for v in (1, 2, 3)}
    version4 = json.loads((ROOT / "config/official-book-sources-v4.json").read_text())
    version4["sources"][:3] = deepcopy(versions[3]["sources"])
    version4["sources"][3]["minimum_detail_pages"] = {"active": 1, "passive": 1}
    versions[4] = version4
    monkeypatch.setattr(book_sources, "REGISTRIES", registries)
    monkeypatch.setattr(book_sources, "CURRENT_VERSION", 4)
    monkeypatch.setattr(
        book_sources,
        "registry",
        lambda version=None, root=None: deepcopy(
            versions[book_sources.CURRENT_VERSION if version is None else version]
        ),
    )


def fixtures(identities):
    originals = []
    groups = []
    for edition, as_of, printed, cover_date in [
        ("October", "2026-09-30", "30th September, 2026", "September 30, 2026"),
        ("September", "2026-08-31", "31st August, 2026", "August 31, 2026"),
    ]:
        rows = catalogue_rows()[:2]
        for row in rows:
            for key in ("name", "url", "doc", "month"):
                row[key] = row[key].replace("October", edition)
        catalogue = {
            "source_url": uti_book.CATALOGUE + "?year=2026&month=" + edition,
            "media_type": "application/json",
            "role": "catalogue",
            "content": json.dumps({"rows": rows}).encode(),
        }
        originals.append(catalogue)
        monthly = []
        for selected in uti_acquisition.catalogue_books(
            catalogue["content"], catalogue["source_url"]
        ):
            raw = ("%PDF-SYNTHETIC-NOT-ISSUER-UTI-" + edition + selected["kind"]).encode()
            originals.append(
                {
                    "source_url": selected["source_url"],
                    "content": raw,
                    "media_type": "application/pdf",
                    "role": "book",
                }
            )
            value = layout(as_of=printed, cover_date=cover_date)
            if selected["kind"] == "passive":
                for page in value["pages"]:
                    for field in ("text", "physical_left_text"):
                        if field in page:
                            page[field] = (
                                page[field]
                                .replace("UTI FLEXI CAP FUND", "UTI NIFTY 50 INDEX FUND")
                                .replace("UTI Flexi Cap Fund", "UTI Nifty 50 Index Fund")
                            )
                    page["text_hash"] = hashlib.sha256(page["text"].encode()).hexdigest()
                    if page.get("physical_left_text"):
                        page["physical_left_hash"] = hashlib.sha256(
                            page["physical_left_text"].encode()
                        ).hexdigest()
            body = {
                **value,
                "source_hash": hashlib.sha256(raw).hexdigest(),
                "policy_hash": fingerprint(uti_book_pdf.policy()),
                "engine_hash": book_sources.specification(uti_book.KEY)["decoder_engine_hash"],
                "document_pages": len(value["pages"]),
            }
            monthly.append(
                {
                    **selected,
                    "catalogue_hash": hashlib.sha256(catalogue["content"]).hexdigest(),
                    "extraction": {**body, "extraction_hash": fingerprint(body)},
                }
            )
        groups.append((as_of, monthly))
    directory = {
        "source_url": uti_book.DIRECTORY,
        "media_type": "application/json",
        "role": "directory",
        "content": b'[{"scheme_id":"273","scheme_name":"UTI Flexi Cap Fund"},{"scheme_id":"345","scheme_name":"UTI Nifty 50 Index Fund"}]',
    }
    originals.append(directory)
    bridges = []
    for sid, name, growth, idcw, isin_g, isin_i in [
        (273, "UTI Flexi Cap Fund", 100669, 100668, "INF789F01513", "INF189A01053"),
        (345, "UTI Nifty 50 Index Fund", 100822, 100823, "INF789F01JN2", "INF789F01JL6"),
    ]:
        url = f"https://portal.amfiindia.com/spages/SSD_{sid}.xml"
        index = {
            "source_url": f"https://www.amfiindia.com/api/schemes/{sid}/documents",
            "role": "index",
            "media_type": "application/json",
            "content": json.dumps(
                {"data": [{"schemeId": str(sid), "summaryXmlUrl": url}]}
            ).encode(),
        }
        raw = f"<SchemeSummaryDocument><SchemeSummary><Fund_Name>{name}</Fund_Name><AMFI_Codes_To_be_phased_out>Regular Plan-Growth-{growth} Regular Plan-IDCW-{idcw}</AMFI_Codes_To_be_phased_out><ISINs>Regular Plan-Growth-{isin_g} Regular Plan-IDCW Payout-{isin_i}</ISINs></SchemeSummary></SchemeSummaryDocument>".encode()
        originals.extend(
            [
                index,
                {
                    "source_url": url,
                    "media_type": "application/xml",
                    "role": "identity",
                    "content": raw,
                },
            ]
        )
        bridges.extend(
            amfi_summary_identity.rows(raw, url, hashlib.sha256(index["content"]).hexdigest())
        )
    batches = [
        uti_batch.compile_batch(
            identities,
            monthly,
            bridges,
            originals,
            as_of=as_of,
            directory_hash=hashlib.sha256(directory["content"]).hexdigest(),
        )
        for as_of, monthly in groups
    ]
    archives = [
        {
            "uri": "gs://tlws_mf_data_source/official-monthly-facts/uti-expanded/"
            + hashlib.sha256(o["content"]).hexdigest()
            + (".pdf" if o["media_type"] == "application/pdf" else ".xml"),
            "sha256": hashlib.sha256(o["content"]).hexdigest(),
            "size": len(o["content"]),
            "generation": "1",
            "md5": "SYNTHETIC-NOT-LIVE",
        }
        for o in originals
        if o["media_type"] in ("application/pdf", "application/xml")
    ]
    return batches, originals, archives


def prepared(dsn, monkeypatch):
    accepted_kotak(dsn, monkeypatch)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        prior = dict(current.configuration(conn))
        preserved = book_preservation.capture(conn, 3)
        original = source_state(conn)
        book_upgrade.install(conn, ROOT)
        identities = book_store.population(conn, uti_book.KEY)
    return identities, fixtures(identities), prior, preserved, original


def publish(dsn, identities, inputs, execution="synthetic-uti"):
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        return book_runtime.publish(
            conn,
            uti_book.KEY,
            "tlws-mf-official-facts-refresh-dev-" + execution,
            NOW,
            identities,
            *inputs,
            availability=[],
        )


def references(batch):
    return [
        {
            "code": o["identity"]["code"],
            "batch_id": batch["batch_id"],
            "as_of": batch["disclosure_as_of"],
            "values": {
                k: f["value"] for k, f in o["result"]["fields"].items() if f["status"] == "accepted"
            },
            "source_values": {
                k: f["value"] for k, f in o["result"]["fields"].items() if f["status"] == "accepted"
            },
        }
        for o in batch["observations"]
        if o["result"]["status"] == "usable"
    ]


@pytest.mark.postgres
def test_two_english_months_separate_TER_no_pending_leak_recovery_and_preservation(
    verified_db, monkeypatch
):
    identities, inputs, prior, preserved, original = prepared(verified_db, monkeypatch)
    run = publish(verified_db, identities, inputs)
    assert len(identities) == 5 and run["status"] == "updated"
    assert publish(verified_db, identities, inputs)["run_id"] == run["run_id"]
    assert [r["denominator"] for r in run["coverage"]] == [5, 5]
    for batch in inputs[0]:
        assert book_facts.coverage(batch)["supplemental_accepted_fields"] == {
            "reported_total_expense_ratio": 4
        }
        assert all(
            o["result"]["fields"]["base_expense_ratio"]["status"] == "unavailable"
            for o in batch["observations"]
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        from mfdataindia.enrichment.book_certification import new_supplemental_oracles

        supplemental = {
            "sources": {uti_book.KEY: {"batch_ids": run["batch_ids"]}},
            "supplemental_oracles": [
                {
                    "code": code,
                    "batch_id": batch["batch_id"],
                    "as_of": batch["disclosure_as_of"],
                    "value": "1.68",
                }
                for batch in inputs[0]
                for code in (100669, 100668, 100822, 100823)
            ],
        }
        checked = new_supplemental_oracles(conn, supplemental)
        assert checked["supplemental_originals_checked"] == 4
        assert checked["supplemental_samples_checked"] == 8
        for defect in ("missing_older", "missing_passive", "missing_current_IDCW", "wrong_value"):
            bad = deepcopy(supplemental)
            if defect == "missing_older":
                bad["supplemental_oracles"] = [
                    o for o in bad["supplemental_oracles"] if o["as_of"] == "2026-09-30"
                ]
            elif defect == "missing_passive":
                bad["supplemental_oracles"] = [
                    o for o in bad["supplemental_oracles"] if o["code"] in (100669, 100668)
                ]
            elif defect == "missing_current_IDCW":
                bad["supplemental_oracles"] = [
                    o
                    for o in bad["supplemental_oracles"]
                    if o["as_of"] != "2026-09-30" or o["code"] in (100669, 100822)
                ]
            else:
                bad["supplemental_oracles"][0]["value"] = "0.99"
            with pytest.raises(ValueError):
                new_supplemental_oracles(conn, bad)
        assert dict(current.configuration(conn)) == prior
        assert current.get(conn, 100669)["fields"]["aum"]["value"] != "22495.18"
        assert book_preservation.capture(conn, 3) == preserved
        activated = book_projection.activate(
            conn, ROOT, references(inputs[0][0]), expected_configuration_hash=fingerprint(prior)
        )
        assert current.get(conn, 100669)["fields"]["aum"]["value"] == "22495.18"
        assert current.get(conn, 100669)["fields"]["base_expense_ratio"]["value"] is None
        assert book_preservation.capture(conn, 3) == preserved
        assert source_state(conn) == original
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert (
            book_projection.recover(conn, ROOT, activated["transition_id"])["status"] == "RECOVERED"
        )
        assert (
            book_projection.recover(conn, ROOT, activated["transition_id"])["status"]
            == "ALREADY_RECOVERED"
        )
        assert book_preservation.capture(conn, 3) == preserved
        assert source_state(conn) == original


@pytest.mark.postgres
@pytest.mark.parametrize(
    "defect",
    [
        "TER_as_BER",
        "Hindi",
        "missing_book",
        "wrong_identity",
        "wrong_decoder",
        "missing_archive",
        "stale_month",
    ],
)
def test_database_rejects_unbound_or_misrepresented_publication(verified_db, monkeypatch, defect):
    identities, inputs, *_ = prepared(verified_db, monkeypatch)
    batches, originals, archives = deepcopy(inputs)
    if defect == "TER_as_BER":
        batches[0]["observations"][0]["result"]["fields"]["base_expense_ratio"] = batches[0][
            "observations"
        ][0]["result"]["reported_total_expense_ratio"]
    elif defect == "Hindi":
        batches[0]["monthly_extractions"][0]["language"] = "Hindi"
        batches[0]["extraction_hash"] = fingerprint(batches[0]["monthly_extractions"])
    elif defect == "missing_book":
        batches[0]["document_refs"] = [
            r for r in batches[0]["document_refs"] if r["role"] != "book"
        ]
    elif defect == "wrong_identity":
        batches[0]["observations"][0]["result"]["identity_evidence"][0]["code"] = 1
    elif defect == "wrong_decoder":
        batches[0]["monthly_extractions"][0]["decoder_engine_hash"] = "0" * 64
        batches[0]["extraction_hash"] = fingerprint(batches[0]["monthly_extractions"])
    elif defect == "missing_archive":
        archives.pop()
    elif defect == "stale_month":
        batches[0]["disclosure_as_of"] = "2026-07-31"
    for batch in batches:
        batch["batch_id"] = fingerprint({k: v for k, v in batch.items() if k != "batch_id"})
    with pytest.raises((psycopg.Error, ValueError)):
        publish(
            verified_db,
            identities,
            (batches, originals, archives),
            execution="bad-" + defect.lower().replace("_", "-"),
        )


def test_all_previous_source_and_executable_bytes_remain_frozen():
    old = book_store.migration_source(version=3).split(
        "    IF spec->>'adapter'='kotak-monthly-book-v1' THEN", 1
    )[1]
    new = book_store.migration_source(version=4).split(
        "    -- Prior source versions 1-3 remain byte-for-byte frozen below.\n", 1
    )[1]
    assert new == "    IF spec->>'adapter'='kotak-monthly-book-v1' THEN" + old
    assert book_projection.PROJECTIONS[4] == book_projection.PROJECTIONS[3]
    assert (
        fingerprint(book_sources.configuration(3))
        == "c2e70369eece3ee715d9ca4f7b035ea9ed6c96a60c3d44f57bdcb1595ab8da9c"
    )
    assert book_sources.registry(4)["maximum_sources_per_execution"] == 6


@pytest.mark.postgres
@pytest.mark.parametrize("role", ROLES)
def test_UTI_upgrade_does_not_expand_runtime_privileges(verified_db, monkeypatch, role):
    prepared(verified_db, monkeypatch)
    with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
        book_store.verify_acl(conn)
        with pytest.raises(ValueError, match="owner"):
            book_upgrade.install(conn, ROOT)
