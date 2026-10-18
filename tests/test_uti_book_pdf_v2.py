"""Original physical titles, unchanged columns and bounded isolated workers."""

import hashlib
import io
from types import SimpleNamespace

import pytest

from mfdataindia.enrichment import (
    _uti_book_pdf_worker as old_worker,
    _uti_book_pdf_worker_v2 as worker,
    book_sources,
    uti_book_pdf,
    uti_book_pdf_v2,
)
from mfdataindia.review.policy import fingerprint
from test_uti_book_pdf import page

TITLE = "UTI BANKING & FINANCIAL SERVICES FUND"


def test_category_never_becomes_part_of_the_original_fund_title():
    value = page(extra=[(TITLE, 15, 756), ("Category", 452, 765), ("Sectoral", 452, 756)])
    old = old_worker.column(value, uti_book_pdf.policy())
    new = worker.column(value, uti_book_pdf_v2.policy())
    assert {k: v for k, v in new.items() if k != "header"} == old
    assert new["header"]["text"] == TITLE
    assert new["header"]["text_hash"] == hashlib.sha256(TITLE.encode()).hexdigest()
    assert new["geometry"] == old["geometry"]


@pytest.mark.parametrize("title,x,y", [
    (TITLE, 60, 756), (TITLE, 15, 710), (TITLE, 220, 680),
    ("UTI Corporate Bond Fund-Direct Growth", 220, 681),
    ("Category", 15, 756), (TITLE + "\nSecond fund", 15, 756),
    ("UTI " + "A" * 350, 15, 756),
])
def test_no_holding_category_multiline_or_outside_header_region(title, x, y):
    result = worker.column(page(extra=[(title, x, y)]), uti_book_pdf_v2.policy())
    assert result["header"] is None


def test_two_physical_headers_are_ambiguous_even_if_identical():
    result = worker.column(page(extra=[(TITLE, 15, 756)] * 2), uti_book_pdf_v2.policy())
    assert result["header"] is None


@pytest.mark.parametrize("anchors,x", [(0, 220), (2, 220), (1, 100), (1, 400)])
def test_title_never_rescues_a_missing_or_ambiguous_field_column(anchors, x):
    assert worker.column(page(extra=[(TITLE, 15, 756)], anchors=anchors, anchor_x=x), uti_book_pdf_v2.policy()) is None


def test_field_multiline_and_baseline_rules_are_retained():
    value = page(extra=[(TITLE, 15, 756), ("First\nSecond", 19, 400)])
    assert worker.column(value, uti_book_pdf_v2.policy()) is None


def test_geometry_and_font_transform_are_physical_not_text_order():
    def extract(*, visitor_text):
        # Input order is deliberately unrelated to the original coordinates.
        for text, x, y, scale in [("Regular : 2.38", 19, 200, 8), (TITLE, 15, 756, 16),
                                   ("Portfolio as on 30th September, 2026", 220, 708, 8)]:
            visitor_text(text, [1, 0, 0, 1, 0, 0], [scale, 0, 0, scale, x, y], {}, 1)
    value = SimpleNamespace(mediabox=SimpleNamespace(width=594, height=792), extract_text=extract)
    assert worker.column(value, uti_book_pdf_v2.policy())["header"]["font_points"] == 16


def test_old_policies_engines_and_all_resource_bounds_stay_frozen():
    limits, old = uti_book_pdf_v2.policy(), uti_book_pdf.policy()
    for key in ("worker_wall_seconds", "worker_cpu_seconds", "linux_worker_address_space_bytes", "maximum_pages",
                "maximum_source_bytes", "maximum_page_stream_bytes", "maximum_total_stream_bytes", "maximum_geometry_output_bytes"):
        assert limits[key] == old[key]
    assert limits["version"] == 2 and limits["retained_field_column_policy_hash"] == fingerprint(old)
    assert fingerprint(book_sources.configuration(4)) == "6fb6c0c804a1dac20e4e2c4aa25f3cd107ce7c58517164409ae2da52e97869d4"
    assert fingerprint(book_sources.current_policy(4)) == "2b1d478498a72a267ecd4719300a5456db52fa0764b2a446e9ee75b545839e5a"


def real_fixture(*, fractional=False):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    value = writer.add_blank_page(width=594, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    value[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    commands = []
    title_geometry = (15.624, 756.5192, 18.0) if fractional else (15, 756, 16)
    for text, x, y, size in [(TITLE, *title_geometry), ("Category", 452, 765, 8), ("Sectoral", 452, 746, 8),
                            ("Portfolio as on 30th September, 2026", 220, 708, 8),
                            ("Benchmark Index", 19, 650, 8), ("Nifty Financial Services TRI", 19, 635, 8),
                            ("Fund Manager", 19, 620, 8), ("Regular : 2.38", 19, 200, 8)]:
        commands.append(f"BT /F1 {size} Tf {x} {y} Td ({text}) Tj ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode())
    value.replace_contents(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_real_isolated_v2_workers_keep_v1_field_geometry():
    raw = real_fixture()
    old, new = uti_book_pdf.extract(raw), uti_book_pdf_v2.extract(raw)
    assert old["source_hash"] == new["source_hash"] == hashlib.sha256(raw).hexdigest()
    assert new["pages"][0]["physical_title"]["text"] == TITLE
    for key in ("text", "text_hash", "physical_left_text", "physical_left_hash", "physical_geometry"):
        assert new["pages"][0][key] == old["pages"][0][key]
    assert new["policy_hash"] == fingerprint(uti_book_pdf_v2.policy())
    assert new["engine_hash"] == uti_book_pdf_v2.engine_hash()
    assert new["whole_document_semantic_coverage"] is False
