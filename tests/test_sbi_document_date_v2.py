"""Provider-free SBI dual-date parser and batch proofs."""

import hashlib

import pytest

from mfdataindia.enrichment import (
    amc_book_pdf,
    book_sources,
    sbi_batch_v2,
    sbi_book_v2,
)
from mfdataindia.review.policy import fingerprint
from test_sbi_book import ACTIVE, IDENTITY

PERIOD = "2026-10-31"
DOCUMENT_DATE = "2026-10-30"


def cover(text="FACTSHEET AS ON OCTOBER 30th, 2026"):
    return {
        "page": 1,
        "text": text,
        "text_hash": hashlib.sha256(text.encode()).hexdigest(),
    }


def test_exact_pdf_date_is_separate_from_month_end_field_identity():
    detail_text = ACTIVE.replace("30/09/2026", "30/10/2026")
    detail = {
        "source_hash": "a" * 64,
        "pages": [
            {
                "page": 18,
                "text": detail_text,
                "text_hash": hashlib.sha256(detail_text.encode()).hexdigest(),
            }
        ],
    }
    bridges = [
        {
            "fund_name": "SBI Flexicap Fund",
            "code": 103215,
            "isin": "INF200K01222",
            "label": "Regular Plan - Growth",
            "source_hash": "b" * 64,
            "source_url": "https://portal.amfiindia.com/spages/SSD_11605.xml",
            "locator": "/root/Options_Names/item[1]",
            "index_hash": "c" * 64,
        }
    ]
    result = sbi_book_v2.derive(
        IDENTITY,
        bridges,
        sbi_book_v2.detail_pages(detail),
        PERIOD,
        DOCUMENT_DATE,
    )
    assert result["status"] == "usable"
    assert all(field["reporting_period"] == PERIOD for field in result["fields"].values())
    assert all(field["disclosure_as_of"] == PERIOD for field in result["fields"].values())
    assert all(field["document_as_of"] == DOCUMENT_DATE for field in result["fields"].values())
    assert result["fields"]["aum"]["field_effective_date"] == DOCUMENT_DATE
    assert result["fields"]["risk_level"]["field_effective_date"] is None


@pytest.mark.parametrize(
    "active,passive,period,message",
    [
        ("cover without date", "FACTSHEET AS ON OCTOBER 30th, 2026", PERIOD, "unique"),
        (
            "Factsheet as on October 30, 2026\nFactsheet as on October 29, 2026",
            "FACTSHEET AS ON OCTOBER 30th, 2026",
            PERIOD,
            "unique",
        ),
        (
            "Factsheet as on October 30, 2026",
            "FACTSHEET AS ON OCTOBER 29th, 2026",
            PERIOD,
            "differ",
        ),
        (
            "Factsheet as on September 30, 2026",
            "FACTSHEET AS ON SEPTEMBER 30th, 2026",
            PERIOD,
            "outside",
        ),
        (
            "Factsheet as on October 30, 2026",
            "Factsheet as on October 30, 2026",
            "2026-10-30",
            "month-end",
        ),
    ],
)
def test_document_dates_fail_closed(active, passive, period, message):
    with pytest.raises(ValueError, match=message):
        sbi_book_v2.paired_document_dates(
            {
                "active": {"pages": [cover(active)]},
                "passive": {"pages": [cover(passive)]},
            },
            period,
        )


def extraction(role, source_hash, detail_text):
    pages = [cover()]
    for number in range(10):
        text = detail_text.replace(
            "SBI FLEXICAP FUND",
            "SBI FLEXICAP FUND"
            if role == "active" and number == 0
            else f"SBI SYNTHETIC {chr(65 + number)} FUND",
        )
        pages.append(
            {
                "page": number + 2,
                "text": text,
                "text_hash": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
    body = {
        "source_hash": source_hash,
        "pages": pages,
        "policy_hash": fingerprint(amc_book_pdf.policy()),
    }
    return {**body, "extraction_hash": fingerprint(body)}


def test_batch_v3_seals_both_dates_without_relabelling_period():
    detail_text = ACTIVE.replace("30/09/2026", "30/10/2026")
    book_bytes = [
        b"%PDF-current-active",
        b"%PDF-current-passive",
        b"%PDF-old-active",
        b"%PDF-old-passive",
    ]
    book_hashes = [hashlib.sha256(value).hexdigest() for value in book_bytes]
    active_url = "https://www.sbimf.com/docs/default-source/scheme-factsheets/all-sbimf-schemes-factsheet-october-2026.pdf"
    passive_url = "https://www.sbimf.com/docs/default-source/scheme-factsheets/sbi-mf-passives-(index-etf-fof)-factsheet-october-2026.pdf"
    monthly = {
        "active": {
            "source_url": active_url,
            "extraction": extraction("active", book_hashes[0], detail_text),
        },
        "passive": {
            "source_url": passive_url,
            "extraction": extraction("passive", book_hashes[1], detail_text),
        },
    }
    dates = sbi_book_v2.paired_document_dates(
        {role: item["extraction"] for role, item in monthly.items()}, PERIOD
    )
    directory = b"[]"
    index = b'{"data":[]}'
    identity = b"<root/>"
    originals = [
        {
            "source_url": sbi_book_v2.CATALOGUE,
            "media_type": "text/html",
            "role": "catalogue",
            "content": b"links",
        },
        {
            "source_url": sbi_book_v2.DIRECTORY,
            "media_type": "application/json",
            "role": "directory",
            "content": directory,
        },
        {
            "source_url": "https://www.amfiindia.com/api/schemes/11605/documents",
            "media_type": "application/json",
            "role": "index",
            "content": index,
        },
        {
            "source_url": "https://portal.amfiindia.com/spages/SSD_11605.xml",
            "media_type": "application/xml",
            "role": "identity",
            "content": identity,
        },
        *[
            {
                "source_url": url,
                "media_type": "application/pdf",
                "role": "book",
                "content": content,
            }
            for url, content in zip(
                [
                    active_url,
                    passive_url,
                    active_url.replace("october", "september"),
                    passive_url.replace("october", "september"),
                ],
                book_bytes,
            )
        ],
    ]
    bridges = [
        {
            "fund_name": "SBI Flexicap Fund",
            "code": 103215,
            "isin": "INF200K01222",
            "label": "Regular Plan - Growth",
            "source_hash": hashlib.sha256(identity).hexdigest(),
            "source_url": "https://portal.amfiindia.com/spages/SSD_11605.xml",
            "locator": "/root/Options_Names/item[1]",
            "index_hash": hashlib.sha256(index).hexdigest(),
        }
    ]
    with book_sources.select_registry(9):
        batch = sbi_batch_v2.compile_batch(
            [IDENTITY],
            monthly,
            bridges,
            originals,
            reporting_period=PERIOD,
            document_dates=dates,
            directory_hash=hashlib.sha256(directory).hexdigest(),
        )
    assert batch["format"] == "official-amc-book-batch-v3"
    assert batch["reporting_period"] == batch["disclosure_as_of"] == PERIOD
    assert batch["document_as_of"] == DOCUMENT_DATE
    assert {item["document_as_of"] for item in batch["monthly_extractions"]} == {DOCUMENT_DATE}
    fields = batch["observations"][0]["result"]["fields"]
    assert all(field["disclosure_as_of"] == PERIOD for field in fields.values())
    assert all(field["document_as_of"] == DOCUMENT_DATE for field in fields.values())
