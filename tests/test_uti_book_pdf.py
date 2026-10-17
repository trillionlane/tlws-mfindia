"""Physical column identity/grouping without network or filesystem writes."""

from types import SimpleNamespace
import hashlib
import io

import pytest

from mfdataindia.enrichment import _uti_book_pdf_worker as worker, uti_book_pdf


def page(*, extra=(), anchor_x=220, anchors=1):
    spans = [("Portfolio as on 30th September, 2026", anchor_x, 708)] * anchors + [
        ("Closing AUM :", 19, 361.641),
        ("`", 122, 361.601),
        ("28,063.55", 128, 362.058),
        ("Crore", 163, 361.641),
        ("Regular :", 19, 237.757),
        ("0.37", 131, 237.800),
        ("Wrong Portfolio Ltd. 5.00", 219.28, 361.9),
        *extra,
    ]

    def extract(*, visitor_text):
        for text, x, y in spans:
            visitor_text(text, [1, 0, 0, 1, 0, 0], [8, 0, 0, 8, x, y], {}, 1)

    return SimpleNamespace(mediabox=SimpleNamespace(width=594, height=792), extract_text=extract)


def test_label_amount_and_unit_baselines_merge_but_adjacent_portfolio_does_not():
    result = worker.column(page(), uti_book_pdf.policy())
    assert result["text"] == "Closing AUM : ` 28,063.55 Crore\nRegular : 0.37"
    assert result["geometry"]["boundary_x"] == 204
    assert result["geometry"]["baseline_tolerance_points"] == 0.75


@pytest.mark.parametrize("anchors,anchor_x", [(0, 220), (2, 220), (1, 100), (1, 400)])
def test_ambiguous_or_outside_reviewed_anchor_region_has_no_column(anchors, anchor_x):
    assert worker.column(page(anchors=anchors, anchor_x=anchor_x), uti_book_pdf.policy()) is None


def test_multiline_callback_cannot_hide_a_partial_manager_or_field():
    assert (
        worker.column(
            page(extra=[("Mr. First Manager\nMr. Second Manager", 19, 400)]), uti_book_pdf.policy()
        )
        is None
    )


def test_adjacent_vertical_rows_are_not_combined():
    result = worker.column(page(extra=[("Separate row", 19, 359)]), uti_book_pdf.policy())
    assert "Crore\nSeparate row\nRegular" in result["text"]


def test_decoder_never_changes_frozen_shared_profile_or_uses_layout_fallback():
    policy = uti_book_pdf.policy()
    assert policy["maximum_source_bytes"] == 16777216
    assert "no guessed layout-text fallback" in policy["ambiguity"]
    assert len(uti_book_pdf.engine_hash()) == 64


def test_real_isolated_workers_accept_explicit_empty_content_stream():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject
    from mfdataindia.enrichment import book_sources, uti_book
    from mfdataindia.review.policy import fingerprint

    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(b"")
    page.replace_contents(stream)
    output = io.BytesIO()
    writer.write(output)
    raw = output.getvalue()
    result = uti_book_pdf.extract(raw)
    assert result["source_hash"] == hashlib.sha256(raw).hexdigest()
    assert result["document_pages"] == 1
    assert result["pages"][0]["text"] == ""
    assert result["pages"][0]["physical_left_text"] is None
    assert result["policy_hash"] == fingerprint(uti_book_pdf.policy())
    assert result["engine_hash"] == book_sources.specification(uti_book.KEY)["decoder_engine_hash"]
    assert result["whole_document_semantic_coverage"] is False


def test_missing_content_stream_still_fails_closed_without_source_repair():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(ValueError, match="layout book decoder failed"):
        uti_book_pdf.extract(output.getvalue())
