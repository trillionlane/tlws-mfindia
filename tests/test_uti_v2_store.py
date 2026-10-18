"""Disposable database proofs only; synthetic titles are NOT issuer evidence."""

from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import (
    amfi_summary_identity,
    book_certification,
    book_preservation,
    book_preservation_v2,
    book_projection,
    book_sources,
    book_store,
    book_upgrade,
    current_facts_store as current,
    uti_batch_v2,
    uti_book,
    uti_book_pdf_v2,
)
from mfdataindia.review.policy import fingerprint
from test_amc_book_facts import ROOT
import test_uti_store as old
from test_verified_review_api import runtime_dsn
from test_verified_review_store import ROLES, source_state, verified_db as verified_db


def prepare_v4(dsn, monkeypatch, *, canonical=False, aum_rows=False):
    registries = book_sources.REGISTRIES
    old.accepted_kotak(dsn, monkeypatch)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        amc = conn.execute(
            "SELECT amc_id FROM mf.amcs WHERE amfi_amc_name=%s", (uti_book.AMC,)
        ).fetchone()["amc_id"]
        for code, option, isin in (
            (102397, "IDCW", "INF789F01273"),
            (102398, "GROWTH", "INF789F01299"),
        ):
            conn.execute(
                "INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout) VALUES(%s,%s,%s,%s,'OPEN_ENDED','Equity','REGULAR',%s,%s)",
                (
                    code,
                    amc,
                    "UTI Transportation and Logistics Fund - Regular Plan - " + option,
                    "UTI TRANSPORTATION AND LOGISTICS FUND",
                    option,
                    isin,
                ),
            )
        book_upgrade.install(conn, ROOT)
        identities = book_store.population(conn, uti_book.KEY)
    calls = []
    compile_v1 = old.uti_batch.compile_batch

    def capture(*args, **kwargs):
        calls.append(deepcopy((args, kwargs)))
        return compile_v1(*args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(old.uti_batch, "compile_batch", capture)
        inputs = old.fixtures(identities)
    old.publish(dsn, identities, inputs)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        # Publication is a short READ COMMITTED owner transaction. Only the
        # separate source-installation/preservation snapshot uses REPEATABLE READ.
        book_projection.activate(
            conn,
            ROOT,
            old.references(inputs[0][0]),
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
        baseline = dict(current.configuration(conn))
        original = source_state(conn)
        inventory = book_store.capture(conn)
    # Actual successive acquisitions have distinct observation times. Do not
    # let a synthetic same-time run-id hash tie choose the predecessor receipt.
    monkeypatch.setattr(old, "NOW", old.NOW + timedelta(seconds=1))
    versions = {v: book_sources.registry(v) for v in (1, 2, 3, 4)}
    filename = "official-book-sources-v5-aum-row.json" if aum_rows else "official-book-sources-v5-canonical.json"
    version5 = json.loads((ROOT / "config" / filename).read_text())
    version5["sources"] = deepcopy(versions[4]["sources"])
    version5["sources"][-1]["adapter"] = "uti-english-monthly-book-v4" if aum_rows else ("uti-english-monthly-book-v3" if canonical else "uti-english-monthly-book-v2")
    version5["authority_transition"]["previous_registry_hash"] = fingerprint(versions[4])
    version5["authority_transition"]["previous_specification_hash"] = fingerprint(
        book_sources.specification(uti_book.KEY, version=4)
    )
    versions[5] = version5
    monkeypatch.setattr(book_sources, "REGISTRIES", registries)
    monkeypatch.setattr(book_sources, "CURRENT_VERSION", 5)
    monkeypatch.setattr(
        book_sources,
        "registry",
        lambda version=None, root=None: deepcopy(versions[5 if version is None else version]),
    )
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        prior = book_preservation.capture(conn, 4)
        book_upgrade.install(conn, ROOT)
        # Installed5 must NOT select5 before activation; the actual policy is4.
        assert book_preservation.capture(conn, 4) == prior
    return identities, calls, baseline, prior, original, inventory


def inputs_v2(identities, calls, *, drop_old_aum=False, canonical=False, aum_rows=False):
    originals = deepcopy(calls[0][0][3])
    directory = next(o for o in originals if o["role"] == "directory")
    directory["content"] = json.dumps(
        [
            *json.loads(directory["content"]),
            {"scheme_id": "999", "scheme_name": "UTI Transportation and Logistics Fund"},
        ]
    ).encode()
    url = "https://portal.amfiindia.com/spages/SSD_999.xml"
    index = {
        "source_url": "https://www.amfiindia.com/api/schemes/999/documents",
        "role": "index",
        "media_type": "application/json",
        "content": json.dumps({"data": [{"schemeId": "999", "summaryXmlUrl": url}]}).encode(),
    }
    raw = b"<SchemeSummaryDocument><SchemeSummary><Fund_Name>UTI Transportation and Logistics Fund</Fund_Name><AMFI_Codes_To_be_phased_out>Regular Plan-Growth-102398 Regular Plan-IDCW-102397</AMFI_Codes_To_be_phased_out><ISINs>Regular Plan-Growth-INF789F01299 Regular Plan-IDCW Payout-INF789F01273</ISINs></SchemeSummary></SchemeSummaryDocument>"
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
    bridges = [
        *calls[0][0][2],
        *amfi_summary_identity.rows(raw, url, hashlib.sha256(index["content"]).hexdigest()),
    ]
    batches = []
    for args, kwargs in deepcopy(calls):
        monthly = args[1]
        for record in monthly:
            extracted = record["extraction"]
            if record["kind"] == "active":
                page = deepcopy(extracted["pages"][1])
                page["page"] = 30
                page["text"] = page["text"].replace(
                    "UTI FLEXI CAP FUND", "UTI TRANSPORTATION AND LOGISTICS FUND CATEGORY COLLISION"
                )
                page["text_hash"] = hashlib.sha256(page["text"].encode()).hexdigest()
                page["physical_left_text"] = page["physical_left_text"].replace(
                    "UTI FLEXI CAP FUND", "UTI TRANSPORTATION AND LOGISTICS FUND"
                )
                page["physical_left_hash"] = hashlib.sha256(
                    page["physical_left_text"].encode()
                ).hexdigest()
                title = "UTI TRANSPORTATION AND LOGISTICS FUND"
                page["physical_title"] = {
                    "text": title,
                    "text_hash": hashlib.sha256(title.encode()).hexdigest(),
                    "x": 15,
                    "y": 756,
                    "font_points": 16,
                }
                page["physical_geometry"] = {"page_width": 594, "page_height": 792}
                if canonical:
                    page["physical_title"].update(x=15.624, y=756.5192, font_points=18.0)
                    page["physical_geometry"] = {"page_width": 594.0, "page_height": 792.0}
                if aum_rows:
                    from mfdataindia.enrichment import uti_aum_row
                    from test_uti_aum_row import GEOMETRY, spans
                    page["physical_geometry"] = GEOMETRY
                    raw_amount = "4,094.35" if kwargs["as_of"] == "2026-09-30" else "4,341.42"
                    page["physical_left_text"] = page["physical_left_text"].replace(
                        "Closing AUM : ` Crore22,495.18", raw_amount + "\nClosing AUM : ` Crore")
                    original_spans = spans()
                    original_spans[5] = (*original_spans[5][:3], raw_amount)
                    if kwargs["as_of"] == "2026-08-31":
                        original_spans[2] = (*original_spans[2][:3], "Closing AUM :")
                        del original_spans[3]
                    page["physical_aum_row"] = uti_aum_row.select(original_spans, GEOMETRY)
                    page["physical_left_hash"] = hashlib.sha256(page["physical_left_text"].encode()).hexdigest()
                extracted["pages"].append(page)
                if drop_old_aum:
                    old_page = extracted["pages"][1]
                    old_page["physical_left_text"] = old_page["physical_left_text"].replace(
                        "Closing AUM", "Unlabelled balance"
                    )
                    old_page["physical_left_hash"] = hashlib.sha256(
                        old_page["physical_left_text"].encode()
                    ).hexdigest()
            from mfdataindia.enrichment import uti_book_pdf_v3
            decoder = uti_book_pdf_v3 if aum_rows else uti_book_pdf_v2
            extracted["policy_hash"] = fingerprint(decoder.policy())
            extracted["engine_hash"] = decoder.engine_hash()
            extracted["document_pages"] = len(extracted["pages"])
            extracted["extraction_hash"] = fingerprint(
                {k: v for k, v in extracted.items() if k != "extraction_hash"}
            )
        from mfdataindia.enrichment import uti_batch_v3, uti_batch_v4
        compiler = uti_batch_v4 if aum_rows else (uti_batch_v3 if canonical else uti_batch_v2)
        batches.append(
            compiler.compile_batch(
                identities,
                monthly,
                bridges,
                originals,
                as_of=kwargs["as_of"],
                directory_hash=hashlib.sha256(directory["content"]).hexdigest(),
            )
        )
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


@pytest.mark.postgres
@pytest.mark.parametrize("canonical", [False, True, "aum"], ids=["retained-integer-v2", "real-fractional-v3", "complete-original-AUM-row-v4"])
def test_method_upgrade_full_preservation_source_oracles_replay_and_recovery(
    verified_db, monkeypatch, canonical
):
    identities, calls, baseline, prior, original, inventory = prepare_v4(verified_db, monkeypatch, canonical=canonical, aum_rows=canonical == "aum")
    inputs = inputs_v2(identities, calls, canonical=canonical, aum_rows=canonical == "aum")
    run = old.publish(verified_db, identities, inputs, execution="v2-good")
    assert (
        old.publish(verified_db, identities, inputs, execution="v2-good")["run_id"] == run["run_id"]
    )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        for code in (102397, 102398):
            history = book_store.fund(conn, code)["observations"]
            assert len(history) == 4  # Both periods of both authorities retained.
            fresh = [o for o in history if o["matches_installed_specification"]]
            assert len(fresh) == 2 and all(o["in_latest_successful_run"] for o in fresh)
            assert all(o["result"]["status"] == "usable" for o in fresh)
            assert all(
                o["result"]["detail_header_evidence"]["method"] == "unique_original_physical_title"
                for o in fresh
            )
            if canonical:
                for observation in fresh:
                    header = observation["result"]["detail_header_evidence"]
                    assert header["x"] == "15.624" and header["y"] == "756.5192"
                    assert header["font_points"] == "18.0" and header["page_width"] == "594.0"
            if canonical == "aum":
                for observation in fresh:
                    expected_aum = {"2026-09-30": "4094.35", "2026-08-31": "4341.42"}[observation["disclosure_as_of"]]
                    assert observation["result"]["fields"]["aum"]["value"] == expected_aum
                    proof = observation["result"]["detail_aum_row_evidence"]
                    assert proof["row_hash"] == fingerprint(proof["row"])
        assert book_preservation.capture(conn, 4) == prior
        activated = book_projection.activate(
            conn,
            ROOT,
            old.references(inputs[0][0]),
            expected_configuration_hash=fingerprint(baseline),
        )
        preserved = book_preservation_v2.compare(prior, book_preservation.capture(conn, 4))
        assert preserved["revised_source_preservation"][uti_book.KEY]["new_primary_cells"] > 0
        assert (
            activated["target_value_preservation"][uti_book.KEY][
                "supported_primary_period_cells_preserved"
            ]
            > 0
        )
        assert source_state(conn) == original
        after = book_store.capture(conn)
        for table in ("configuration", "sources", "batches", "runs", "transitions"):
            assert all(after[table].get(k) == v for k, v in inventory[table].items())
        assert inventory["original_byte_integrity_checked"] is True
        assert after["original_byte_integrity_checked"] is True
        samples = {
            "sources": {uti_book.KEY: {"batch_ids": run["batch_ids"]}},
            "reviewed": old.references(inputs[0][0]),
            "original_oracles": [
                {
                    "code": s["code"],
                    "batch_id": s["batch_id"],
                    "as_of": s["as_of"],
                    "source_values": s["source_values"],
                }
                for batch in inputs[0]
                for s in old.references(batch)
            ],
        }
        assert (
            book_certification.new_source_oracles(conn, samples)["new_source_original_documents"]
            == 4
        )
        for defect in ("missing_all", "missing_older", "missing_passive", "missing_current_IDCW"):
            bad = deepcopy(samples)
            if defect == "missing_all":
                bad["original_oracles"] = []
            elif defect == "missing_older":
                bad["original_oracles"] = [
                    s for s in bad["original_oracles"] if s["as_of"] == "2026-09-30"
                ]
            elif defect == "missing_passive":
                bad["original_oracles"] = [
                    s for s in bad["original_oracles"] if s["code"] not in (100822, 100823)
                ]
            else:
                bad["reviewed"] = [
                    s for s in bad["reviewed"] if s["code"] in (100669, 100822, 102398)
                ]
            with pytest.raises(ValueError):
                book_certification.new_source_oracles(conn, bad)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert (
            book_projection.recover(conn, ROOT, activated["transition_id"])["status"] == "RECOVERED"
        )
        assert (
            book_projection.recover(conn, ROOT, activated["transition_id"])["status"]
            == "ALREADY_RECOVERED"
        )
        book_preservation_v2.compare(prior, book_preservation.capture(conn, 4))
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        book_upgrade.install(conn, ROOT)
    old.publish(verified_db, identities, inputs, execution="v2-reactivate")
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        book_projection.activate(
            conn,
            ROOT,
            old.references(inputs[0][0]),
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
        book_preservation_v2.compare(prior, book_preservation.capture(conn, 4))
        assert source_state(conn) == original


@pytest.mark.postgres
def test_lost_old_supported_values_abort_activation_transaction(verified_db, monkeypatch):
    identities, calls, baseline, prior, *_ = prepare_v4(verified_db, monkeypatch)
    inputs = inputs_v2(identities, calls, drop_old_aum=True)
    old.publish(verified_db, identities, inputs, execution="v2-loss")
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        inventory = book_store.capture(conn)
        with pytest.raises(ValueError, match="previously supported"):
            book_projection.activate(
                conn, ROOT, [s for s in old.references(inputs[0][0]) if s["code"] == 102398],
                expected_configuration_hash=fingerprint(baseline)
            )
        assert dict(current.configuration(conn)) == baseline
        assert book_store.capture(conn) == inventory
        assert book_preservation.capture(conn, 4) == prior


@pytest.mark.postgres
def test_restricted_roles_and_database_title_guard(verified_db, monkeypatch):
    identities, calls, *_ = prepare_v4(verified_db, monkeypatch)
    valid = inputs_v2(identities, calls)
    for defect in ("hash", "source", "outside", "font", "type", "null"):
        inputs = deepcopy(valid)
        item = next(o for o in inputs[0][0]["observations"] if o["identity"]["code"] == 102398)
        header = item["result"]["detail_header_evidence"]
        if defect == "hash":
            header["text_hash"] = "f" * 64
        elif defect == "source":
            header["source_hash"] = "f" * 64
        elif defect == "outside":
            header["x"] = 300
        elif defect == "font":
            header["font_points"] = 31
        elif defect == "type":
            header["x"] = "15"
        else:
            item["result"]["detail_header_evidence"] = None
        batch = inputs[0][0]
        batch["batch_id"] = fingerprint({k: v for k, v in batch.items() if k != "batch_id"})
        with pytest.raises((psycopg.Error, ValueError)):
            old.publish(verified_db, identities, inputs, execution="v2-bad-" + defect)
    for role in ROLES:
        with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
            book_store.verify_acl(conn)
            with pytest.raises(ValueError, match="owner"):
                book_upgrade.install(conn, ROOT)


def test_frozen_all_historical_migrations_and_exact_projection():
    prior = book_store.migration_source(version=4)
    revised = book_store.migration_source(version=5)
    marker = "    -- Prior source versions 1-3 remain byte-for-byte frozen below.\n"
    assert revised.split(marker, 1)[1] == prior.split(marker, 1)[1]
    assert revised.count("\nBEGIN;\n") == 1 and revised.count("COMMIT;") == 1
    assert book_projection.PROJECTIONS[5] == book_projection.PROJECTIONS[4]
    assert (
        fingerprint(book_sources.configuration(4))
        == "6fb6c0c804a1dac20e4e2c4aa25f3cd107ce7c58517164409ae2da52e97869d4"
    )
