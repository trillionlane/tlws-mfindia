"""Isolated PostgreSQL safety proofs; synthetic originals are not live evidence."""

from copy import deepcopy
import hashlib
import json

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import (
    book_projection,
    book_runtime,
    book_sources,
    book_store,
    book_upgrade,
    current_facts_store as current,
    sbi_book,
    sbi_batch,
    book_preservation,
    amc_book_pdf,
)
from mfdataindia.review.policy import fingerprint
from test_amc_book_facts import ROOT, prepared, publish
from test_verified_review_api import runtime_dsn
from test_verified_review_store import ROLES
from test_verified_review_store import verified_db as verified_db
from test_sbi_book import ACTIVE, XML, catalogue


def accepted_fs08(dsn, monkeypatch):
    identities, inputs, _ = prepared(dsn, monkeypatch)
    publish(dsn, identities, inputs)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        book_projection.activate(
            conn,
            ROOT,
            [
                {
                    "code": 100001,
                    "batch_id": inputs[0][0]["batch_id"],
                    "as_of": "2026-09-30",
                    "values": {"aum": "12345.67"},
                }
            ],
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
        conn.execute(
            "INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES(%s,%s)",
            (sbi_book.AMC, sbi_book.AMC.upper()),
        )
        amc = conn.execute(
            "SELECT amc_id FROM mf.amcs WHERE amfi_amc_name=%s", (sbi_book.AMC,)
        ).fetchone()["amc_id"]
        for code, option, isin in [
            (103215, "GROWTH", "INF200K01222"),
            (103216, "IDCW", "INF200K01248"),
            (102272, "GROWTH", "INF200K01537"),
        ]:
            conn.execute(
                "INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout) VALUES(%s,%s,%s,%s,'OPEN_ENDED','Equity','REGULAR',%s,%s)",
                (
                    code,
                    amc,
                    ("SBI Nifty Index Fund" if code == 102272 else "SBI Flexicap Fund")
                    + " - Regular Plan - "
                    + option,
                    ("SBI NIFTY INDEX FUND" if code == 102272 else "SBI FLEXICAP FUND")
                    + " REGULAR "
                    + option,
                    option,
                    isin,
                ),
            )
        prior_graph = dict(current.configuration(conn))
        old = book_store.capture(conn)
    # Preserve the synthetic v1 source specification exactly in the fixture v2.
    # Production uses hash-pinned registry files; these are labelled test originals.
    reg1 = book_sources.registry(1)
    reg2 = json.loads((ROOT / "config/official-book-sources-v2.json").read_text())
    reg2["sources"][0] = deepcopy(reg1["sources"][0])
    versions = {1: reg1, 2: reg2}
    monkeypatch.setattr(book_sources, "CURRENT_VERSION", 2)
    monkeypatch.setattr(
        book_sources,
        "registry",
        lambda version=None, root=None: deepcopy(
            versions[book_sources.CURRENT_VERSION if version is None else version]
        ),
    )
    return prior_graph, old


def source_inputs(identities, *, aliases=False):
    """Four distinct synthetic PDFs, original catalogue/index/XML and two periods."""
    from datetime import datetime

    catalog = {
        "source_url": sbi_book.CATALOGUE,
        "media_type": "text/html",
        "role": "catalogue",
        "content": catalogue().encode(),
    }
    directory = {
        "source_url": sbi_book.DIRECTORY,
        "media_type": "application/json",
        "role": "directory",
        "content": json.dumps(
            [
                {"scheme_id": "11605", "scheme_name": "SBI Flexicap Fund"},
                {"scheme_id": "11592", "scheme_name": "SBI Nifty Index Fund"},
                *(
                    [{"scheme_id": "11843", "scheme_name": "SBI Nifty Index Fund"}]
                    if aliases
                    else []
                ),
            ]
        ).encode(),
    }
    xml_url = "https://portal.amfiindia.com/spages/SSD_11605.xml"
    index = {
        "source_url": "https://www.amfiindia.com/api/schemes/11605/documents",
        "media_type": "application/json",
        "role": "index",
        "content": json.dumps({"data": [{"schemeId": "11605", "summaryXmlUrl": xml_url}]}).encode(),
    }
    xml = {
        "source_url": xml_url,
        "media_type": "application/xml",
        "role": "identity",
        "content": XML,
    }
    originals = [catalog, directory]
    periods = []
    for edition in sbi_book.editions(catalog["content"], datetime(2026, 10, 17)):
        monthly = {}
        for kind, url in edition["documents"].items():
            raw = b"%PDF-synthetic-not-live-" + (edition["as_of"] + kind).encode()
            original = {
                "source_url": url,
                "media_type": "application/pdf",
                "role": "book",
                "content": raw,
            }
            originals.append(original)
            pages = []
            for n in range(10):
                heading = (
                    ("SBI FLEXICAP FUND" if kind == "active" else "SBI NIFTY INDEX FUND")
                    if n == 0
                    else "SBI SYNTHETIC " + chr(65 + n) + " FUND"
                )
                text = ACTIVE.replace("SBI FLEXICAP FUND", heading)
                if edition["as_of"] == "2026-08-31":
                    text = text.replace("30/09/2026", "31/08/2026")
                pages.append(
                    {
                        "page": n + 1,
                        "text": text,
                        "text_hash": hashlib.sha256(text.encode()).hexdigest(),
                    }
                )
            body = {
                "source_hash": hashlib.sha256(raw).hexdigest(),
                "pages": pages,
                "policy_hash": fingerprint(amc_book_pdf.policy()),
            }
            extraction = {**body, "extraction_hash": fingerprint(body)}
            monthly[kind] = {"source_url": url, "extraction": extraction}
        periods.append((edition["as_of"], monthly))
    originals += [index, xml]
    bridges = sbi_book.identity_rows(XML, xml_url, hashlib.sha256(index["content"]).hexdigest())
    passive_xml_url = "https://portal.amfiindia.com/spages/SSD_11592.xml"
    passive_xml = b"<root><Fund_Name>SBI Nifty Index Fund</Fund_Name><Options_Names><item><AMFICode>102272</AMFICode><ISIN>INF200K01537</ISIN><OptionsNames>Regular Plan - Growth</OptionsNames></item></Options_Names></root>"
    passive_index = {
        "source_url": "https://www.amfiindia.com/api/schemes/11592/documents",
        "media_type": "application/json",
        "role": "index",
        "content": json.dumps(
            {"data": [{"schemeId": "11592", "summaryXmlUrl": passive_xml_url}]}
        ).encode(),
    }
    originals += [
        passive_index,
        {
            "source_url": passive_xml_url,
            "media_type": "application/xml",
            "role": "identity",
            "content": passive_xml,
        },
    ]
    bridges += sbi_book.identity_rows(
        passive_xml, passive_xml_url, hashlib.sha256(passive_index["content"]).hexdigest()
    )
    if aliases:
        alias_url = "https://portal.amfiindia.com/spages/SSD_11843.xml"
        alias_index = {
            "source_url": "https://www.amfiindia.com/api/schemes/11843/documents",
            "media_type": "application/json",
            "role": "index",
            "content": json.dumps(
                {"data": [{"schemeId": "11843", "summaryXmlUrl": alias_url}]}
            ).encode(),
        }
        originals += [
            alias_index,
            {
                "source_url": alias_url,
                "media_type": "application/xml",
                "role": "identity",
                "content": passive_xml,
            },
        ]
        bridges += sbi_book.identity_rows(
            passive_xml, alias_url, hashlib.sha256(alias_index["content"]).hexdigest()
        )
    batches = [
        sbi_batch.compile_batch(
            identities,
            monthly,
            bridges,
            originals,
            as_of=as_of,
            directory_hash=hashlib.sha256(directory["content"]).hexdigest(),
        )
        for as_of, monthly in periods
    ]
    archives = [
        {
            "uri": "gs://tlws_mf_data_source/official-monthly-facts/sbi-expanded/"
            + hashlib.sha256(o["content"]).hexdigest()
            + (".pdf" if o["role"] == "book" else ".xml"),
            "sha256": hashlib.sha256(o["content"]).hexdigest(),
            "generation": "1",
            "size": len(o["content"]),
            "md5": "fixture",
        }
        for o in originals
        if o["media_type"] in ("application/pdf", "application/xml")
    ]
    archives = list({a["sha256"]: a for a in archives}.values())
    return batches, originals, archives


@pytest.mark.postgres
def test_complete_multi_book_publication_projection_recovery_and_reactivation(
    verified_db, monkeypatch
):
    from test_amc_book_facts import NOW

    accepted_fs08(verified_db, monkeypatch)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        preserved = book_preservation.capture(conn, 1)
        book_upgrade.install(conn, ROOT)
        identities = book_store.population(conn, sbi_book.KEY)
    batches, originals, archives = source_inputs(identities)
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        result = book_runtime.publish(
            conn,
            sbi_book.KEY,
            "tlws-mf-official-facts-refresh-dev-synthetic-sbi",
            NOW,
            identities,
            batches,
            originals,
            archives,
            availability=[],
        )
        assert result["status"] == "updated"
    references = [
        {
            "code": code,
            "batch_id": batches[0]["batch_id"],
            "as_of": "2026-09-30",
            "values": {
                "benchmark_name": "BSE 500 (TRI)",
                "fund_managers": ["Mr. Anup Upadhyay"],
                "base_expense_ratio": "1.4",
                "aum": "22109.87",
                "risk_level": "Very High",
            },
            "source_values": {
                "benchmark_name": "BSE 500 (TRI)",
                "fund_managers": ["Mr. Anup Upadhyay"],
                "base_expense_ratio": "1.40",
                "aum": "22109.87",
                "risk_level": "Very High",
            },
        }
        for code in (103215, 103216, 102272)
    ]
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        from mfdataindia.enrichment.book_certification import new_source_oracles

        expected = {
            "sources": {"sbi-expanded": {"batch_ids": [b["batch_id"] for b in batches]}},
            "reviewed": references,
            "original_oracles": [
                {
                    "code": code,
                    "batch_id": b["batch_id"],
                    "as_of": b["disclosure_as_of"],
                    "source_values": references[0]["source_values"],
                }
                for b in batches
                for code in (103215, 102272)
            ],
        }
        assert new_source_oracles(conn, expected)["new_source_original_documents"] == 4
        for defect in ("missing_older", "missing_passive", "only_old_current", "raw_scale"):
            bad = deepcopy(expected)
            if defect == "missing_older":
                bad["original_oracles"] = bad["original_oracles"][:2]
            elif defect == "missing_passive":
                bad["original_oracles"] = [
                    o for o in bad["original_oracles"] if o["code"] != 102272
                ]
            elif defect == "only_old_current":
                bad["reviewed"] = []
            else:
                bad["original_oracles"][0]["source_values"]["base_expense_ratio"] = "1.4"
            with pytest.raises(ValueError):
                new_source_oracles(conn, bad)
        assert book_preservation.capture(conn, 1) == preserved
        activation = book_projection.activate(
            conn,
            ROOT,
            references,
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
        assert current.get(conn, 103215)["fields"]["base_expense_ratio"]["value"] == "1.4"
        assert (
            book_store.fund(conn, 103215)["observations"][0]["result"]["fields"][
                "base_expense_ratio"
            ]["value"]
            == "1.40"
        )
        assert book_preservation.capture(conn, 1) == preserved
        book_projection.recover(conn, ROOT, activation["transition_id"])
        assert book_store.installed_configuration(conn)["document"]["registry"]["version"] == 1
        assert book_preservation.capture(conn, 1) == preserved
        assert (
            book_projection.recover(conn, ROOT, activation["transition_id"])["status"]
            == "ALREADY_RECOVERED"
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        book_upgrade.install(conn, ROOT)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        book_projection.activate(
            conn,
            ROOT,
            references,
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
        assert book_preservation.capture(conn, 1) == preserved
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM mf_book_facts.sources WHERE source_key='sbi-expanded'"
            ).fetchone()["n"]
            == 10
        )


@pytest.mark.postgres
def test_owner_upgrade_preserves_graph_old_rows_and_restores_old_execution_boundary(
    verified_db, monkeypatch
):
    graph, old = accepted_fs08(verified_db, monkeypatch)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        result = book_upgrade.install(conn, ROOT)
        assert result["status"] == "UPGRADED"
        assert dict(current.configuration(conn)) == graph
        assert book_store.installed_configuration(conn)["document"]["registry"]["version"] == 2
        assert book_upgrade.install(conn, ROOT)["status"] == "ALREADY_INSTALLED"
        for key in ("sources", "batches", "runs"):
            assert book_store.capture(conn)[key] == old[key]
        intent = book_upgrade.prior_upgrade(conn, 2)
        book_upgrade.restore(conn, intent)
        assert book_store.installed_configuration(conn)["document"]["registry"]["version"] == 1
        assert dict(current.configuration(conn)) == graph
        assert book_store.capture(conn)["functions"] == old["functions"]
        book_store.verify_acl(conn, principal=ROLES[1])


@pytest.mark.postgres
@pytest.mark.parametrize("role", ROLES)
def test_runtime_roles_cannot_upgrade_or_restore_sources(verified_db, monkeypatch, role):
    accepted_fs08(verified_db, monkeypatch)
    with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
        with pytest.raises(ValueError, match="owner"):
            book_upgrade.install(conn, ROOT)


@pytest.mark.postgres
@pytest.mark.parametrize("defect", ["unregistered_url", "orphan_xml", "foreign_family", "absl_xml"])
def test_generator_cannot_invent_source_url_or_document_parent(verified_db, monkeypatch, defect):
    accepted_fs08(verified_db, monkeypatch)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        book_upgrade.install(conn, ROOT)
    source = {
        "source_url": "https://attacker.invalid/source.xml",
        "content": b"<root/>",
        "media_type": "application/xml",
    }
    key = sbi_book.KEY
    if defect == "orphan_xml":
        source["source_url"] = "https://portal.amfiindia.com/spages/SSD_11605.xml"
    elif defect == "foreign_family":
        source.update(
            source_url="https://www.amfiindia.com/api/schemes/123/documents",
            content=b'{"data":[]}',
            media_type="application/json",
        )
    elif defect == "absl_xml":
        key = "absl-expanded"
        source["source_url"] = "https://portal.amfiindia.com/spages/SSD_11605.xml"
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        with pytest.raises(psycopg.Error):
            book_runtime.retain_source(conn, key, source)


def test_historical_authority_and_database_function_remain_frozen():
    # No synthetic registry monkeypatch here: real compiled historical authority.
    assert (
        fingerprint(book_sources.configuration(1))
        == "6b919e1993a911edac89cffca400d51a8cbd3138d1210c709a5b265ea58d5aba"
    )
    assert (
        fingerprint(book_sources.current_policy(1))
        == "3166a7e28c9a2d833e46cad8a45a994dc3f82a163a7cc5a45899dbf8dd2a1dba"
    )
    assert (
        hashlib.sha256((ROOT / "sql/optional/038_amc_book_facts.sql").read_bytes()).hexdigest()
        == book_store.SCHEMA_SHA256
    )
    assert book_sources.current_policy(2)["previous_policy_hash"] == fingerprint(
        book_sources.current_policy(1)
    )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "defect",
    [
        "missing_xml_ref",
        "wrong_pdf_ref",
        "bad_ratio",
        "missing_archive",
        "duplicate_period",
        "forged_locator",
        "duplicate_extraction_role",
    ],
)
def test_writer_cannot_publish_incomplete_or_mismatched_multi_original_evidence(
    verified_db, monkeypatch, defect
):
    from test_amc_book_facts import NOW

    accepted_fs08(verified_db, monkeypatch)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        book_upgrade.install(conn, ROOT)
        identities = book_store.population(conn, sbi_book.KEY)
    batches, originals, archives = source_inputs(identities)
    if defect == "missing_archive":
        archives.pop()
    elif defect == "duplicate_period":
        batches[1]["disclosure_as_of"] = batches[0]["disclosure_as_of"]
        for row in batches[1]["observations"]:
            for field in row["result"]["fields"].values():
                field["disclosure_as_of"] = batches[0]["disclosure_as_of"]
                if field["field_effective_date"]:
                    field["field_effective_date"] = batches[0]["disclosure_as_of"]
    else:
        batch = batches[0]
        if defect == "missing_xml_ref":
            batch["document_refs"] = [r for r in batch["document_refs"] if r["role"] != "identity"]
        elif defect == "duplicate_extraction_role":
            batch["monthly_extractions"][1]["role"] = "active"
            batch["extraction_hash"] = fingerprint(batch["monthly_extractions"])
        else:
            row = batch["observations"][0]["result"]
            if defect == "wrong_pdf_ref":
                row["fields"]["aum"]["content_hash"] = "f" * 64
            elif defect == "bad_ratio":
                row["fields"]["base_expense_ratio"]["value"] = "10.01"
            else:
                row["identity_evidence"][0]["locator"] = "/invented/pdf/page[1]"
    for batch in batches:
        batch["batch_id"] = fingerprint({k: v for k, v in batch.items() if k != "batch_id"})
    with psycopg.connect(
        runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row, autocommit=True
    ) as conn:
        with pytest.raises((psycopg.Error, ValueError)):
            with conn.transaction():
                book_runtime.publish(
                    conn,
                    sbi_book.KEY,
                    "tlws-mf-official-facts-refresh-dev-invalid-sbi",
                    NOW,
                    identities,
                    batches,
                    originals,
                    archives,
                    availability=[],
                )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM mf_book_facts.runs WHERE source_key=%s", (sbi_book.KEY,)
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM mf_book_facts.sources WHERE source_key=%s",
                (sbi_book.KEY,),
            ).fetchone()["n"]
            == 0
        )


@pytest.mark.postgres
def test_failed_pre_activation_stage_has_its_own_idempotent_source_recovery(
    verified_db, monkeypatch
):
    graph, old = accepted_fs08(verified_db, monkeypatch)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        installed = book_upgrade.install(conn, ROOT)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert (
            book_upgrade.recover_unactivated(conn, installed["upgrade_id"])["status"] == "RECOVERED"
        )
        assert (
            book_upgrade.recover_unactivated(conn, installed["upgrade_id"])["status"]
            == "ALREADY_RECOVERED"
        )
        assert dict(current.configuration(conn)) == graph
        assert book_store.capture(conn)["functions"] == old["functions"]


@pytest.mark.postgres
@pytest.mark.parametrize("forged", [False, True])
def test_identical_xml_aliases_preserve_first_original_url_and_require_real_parent_edges(
    verified_db, monkeypatch, forged
):
    from test_amc_book_facts import NOW

    accepted_fs08(verified_db, monkeypatch)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        book_upgrade.install(conn, ROOT)
        identities = book_store.population(conn, sbi_book.KEY)
    batches, originals, archives = source_inputs(identities, aliases=True)
    if forged:
        for batch in batches:
            edge = next(
                r for r in batch["document_refs"] if r["source_url"].endswith("SSD_11843.xml")
            )
            edge["source_url"] = "https://portal.amfiindia.com/spages/SSD_999999.xml"
            batch["batch_id"] = fingerprint({k: v for k, v in batch.items() if k != "batch_id"})
    with psycopg.connect(
        runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row, autocommit=True
    ) as conn:

        def append():
            with conn.transaction():
                return book_runtime.publish(
                    conn,
                    sbi_book.KEY,
                    "tlws-mf-official-facts-refresh-dev-alias-sbi",
                    NOW,
                    identities,
                    batches,
                    originals,
                    archives,
                    availability=[],
                )

        if forged:
            with pytest.raises(psycopg.Error):
                append()
        else:
            assert append()["status"] == "updated"
            xml = next(o for o in originals if o["source_url"].endswith("SSD_11592.xml"))
            saved = conn.execute(
                "SELECT source_url FROM mf_book_facts.sources WHERE source_key=%s AND content_hash=%s",
                (sbi_book.KEY, hashlib.sha256(xml["content"]).hexdigest()),
            ).fetchone()
            assert saved["source_url"] == xml["source_url"]
            facts = book_store.fund(conn, 102272)
            evidence = facts["observations"][0]["result"]["identity_evidence"]
            assert {e["source_url"] for e in evidence} == {
                xml["source_url"],
                "https://portal.amfiindia.com/spages/SSD_11843.xml",
            }
            assert len(archives) == 6


@pytest.mark.postgres
def test_unactivated_recovery_does_not_depend_on_failed_candidate_specification(
    verified_db, monkeypatch
):
    accepted_fs08(verified_db, monkeypatch)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        upgrade = book_upgrade.install(conn, ROOT)
    actual = book_sources.configuration

    def drift(version=None):
        document = actual(version)
        if document["registry"]["version"] == 2:
            document["source_specifications"]["sbi-expanded"]["parser_engine_hash"] = "f" * 64
        return document

    monkeypatch.setattr(book_sources, "configuration", drift)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        with pytest.raises(ValueError, match="authority"):
            book_store.installed_configuration(conn)
        assert (
            book_upgrade.recover_unactivated(conn, upgrade["upgrade_id"])["status"] == "RECOVERED"
        )
        assert book_store.installed_configuration(conn)["document"]["registry"]["version"] == 1
