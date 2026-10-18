"""Official-layout regression without relaxing frozen line or financial rules."""

from copy import deepcopy
from decimal import localcontext
import hashlib
import io
from types import SimpleNamespace

import pytest

from mfdataindia.enrichment import uti_aum_row as row, uti_book_pdf_v2, uti_book_pdf_v3, uti_book_v3, uti_book_v4

GEOMETRY = {"page_width": 594.0, "page_height": 792.0, "boundary_x": 203.96, "anchor_y": 708.8738}


def spans():
    # Exact independently read September PDF coordinates, not parser-derived
    # expected values. Monthly average is deliberately a different amount.
    return [(19.624, 428.7218, 8, "Fund Size Monthly Average"),
            (131.384, 429.7381, 8, "4,207.67"),
            (19.624, 418.6418, 8, "Closing AUM"), (121.504, 418.6418, 8, ":"),
            (126.0, 418.6018, 8, "`"), (131.384, 419.7141, 8, "4,094.35"),
            (162.992, 418.6418, 8, "Crore"), (19.624, 408.5538, 8, "No. of Folios"),
            (127.7717, 408.615, 8, "2,13,170")]


def test_independent_official_fractional_row_retains_closing_not_average():
    original = spans()
    expected = row.select(original, GEOMETRY)
    assert expected["spans"][3]["text"] == "4,094.35"
    assert expected["spans"][3]["y"] == "419.7141"
    for precision in (1, 6, 28, 100):
        with localcontext() as context:
            context.prec = precision
            assert row.select(list(reversed(original)), GEOMETRY) == expected
    assert original == spans()


def test_august_original_combined_label_and_colon_are_not_split_or_synthesized():
    data = [s for i, s in enumerate(spans()) if i != 3]
    data[2] = (19.623999999999967, 418.64179999999993, 8.0, "Closing AUM :")
    data[4] = (131.38400000000001, 419.7141, 8.0, "4,341.42")
    evidence = row.select(data, GEOMETRY)
    assert len(evidence['spans']) == 4
    assert evidence['spans'][0]['text'] == 'Closing AUM :'
    assert evidence['spans'][-2]['text'] == '4,341.42'
    assert evidence['spans'][0]['x'] == '19.623999999999967'


@pytest.mark.parametrize("index", range(2, 7))
def test_missing_any_original_row_span_is_unavailable(index):
    data = spans()
    del data[index]
    assert row.select(data, GEOMETRY) is None


@pytest.mark.parametrize("index", range(2, 7))
def test_duplicate_original_row_span_is_ambiguous(index):
    data = spans()
    data.append(data[index])
    assert row.select(data, GEOMETRY) is None


@pytest.mark.parametrize("index,text", [(2, "Average AUM"), (4, "$"), (5, "4,09.35"), (5, "0"),
                                        (5, "-4094.35"), (5, "1e9"), (5, "1,000,000,000,000.01"),
                                        (6, "million"), (6, "Crores"), (5, "4094.35\n9999")])
def test_no_wrong_field_currency_number_unit_or_multiline_inference(index, text):
    data = spans()
    x, y, em, _ = data[index]
    data[index] = (x, y, em, text)
    assert row.select(data, GEOMETRY) is None


@pytest.mark.parametrize("extra", [(131, 418.7, 8, "9999"), (131, 418.7, 8, "Other fund"),
                                   (19, 390, 8, "Closing AUM")])
def test_no_extra_number_text_or_second_closing_label(extra):
    assert row.select([*spans(), extra], GEOMETRY) is None


@pytest.mark.parametrize("index,x,y,em", [(5, 131, 421, 8), (5, 204, 419, 8), (5, 100, 419, 8),
                                        (5, 131, 419, 17), (2, 19, 418, 3), (5, 131, float("nan"), 8)])
def test_no_nearest_amount_cross_row_outside_column_order_or_nonfinite(index, x, y, em):
    data = spans()
    data[index] = (x, y, em, data[index][3])
    assert row.select(data, GEOMETRY) is None


def test_only_original_left_region_spans_not_adjacent_holdings():
    data = spans()

    def extract(*, visitor_text):
        for x, y, em, text in [*data, (219.28, 418.8, 8, "99999"), (220, 418.8, 8, "Closing AUM")]:
            visitor_text(text, [1, 0, 0, 1, 0, 0], [em, 0, 0, em, x, y], {}, 1)
    page = SimpleNamespace(extract_text=extract)
    assert row.page_row(page, GEOMETRY, uti_book_pdf_v3.policy()) == row.select(data, GEOMETRY)


@pytest.mark.parametrize("mutation", ["extra", "numeric_coordinate", "width", "type", "method", "span_key"])
def test_serialized_evidence_is_revalidated(mutation):
    evidence = row.select(spans(), GEOMETRY)
    if mutation == "extra":
        evidence["extra"] = True
    elif mutation == "numeric_coordinate":
        evidence["spans"][0]["x"] = 19.624
    elif mutation == "width":
        evidence["page_width"] = "2001"
    elif mutation == "type":
        evidence["spans"][0]["text"] = None
    elif mutation == "method":
        evidence["method"] = "nearest_number"
    else:
        evidence["spans"][0]["inferred"] = True
    with pytest.raises(ValueError):
        row.validate(evidence)


def setup_derive():
    from test_uti_book_v2 import fixture

    extraction, fund, bridges = fixture()
    page = extraction["pages"][1]
    page["physical_geometry"] = GEOMETRY
    page["physical_left_text"] = page["physical_left_text"].replace("Closing AUM : ` Crore22,495.18", "4,094.35\nClosing AUM : ` Crore")
    # Keep source layout exactly like the official failing row, but the test's
    # fund identity remains explicitly synthetic and not coverage evidence.
    assert "22,495.18" not in page["physical_left_text"]
    page["physical_aum_row"] = row.select(spans(), GEOMETRY)
    details = uti_book_v4.detail_pages(extraction)
    for detail in details:
        detail["source_url"] = "https://source.invalid/synthetic-not-a-real-original.pdf"
    return extraction, fund, bridges, details


def test_versioned_derive_only_fills_unavailable_aum_all_other_fields_unchanged():
    extraction, fund, bridges, details = setup_derive()
    before = deepcopy((extraction, fund, bridges, details))
    old = uti_book_v3.derive(fund, bridges, details, [extraction], "2026-09-30")
    new = uti_book_v4.derive(fund, bridges, details, [extraction], "2026-09-30")
    assert old["fields"]["aum"]["status"] == "unavailable"
    assert new["fields"]["aum"]["value"] == "4094.35"
    assert new["fields"]["aum"]["unit"] == "INR_crore"
    assert new["fields"]["aum"]["field_effective_date"] == "2026-09-30"
    assert {k: v for k, v in new["fields"].items() if k != "aum"} == {k: v for k, v in old["fields"].items() if k != "aum"}
    assert {k: v for k, v in new.items() if k not in ("fields", "detail_aum_row_evidence")} == {k: v for k, v in old.items() if k != "fields"}
    assert (extraction, fund, bridges, details) == before


def test_never_replace_a_prior_accepted_aum_even_with_a_conflicting_sidecar():
    extraction, fund, bridges, details = setup_derive()
    details[0]["left"] = details[0]["left"].replace("4,094.35\nClosing AUM : ` Crore", "Closing AUM : ` Crore 22495.18")
    old = uti_book_v3.derive(fund, bridges, details, [extraction], "2026-09-30")
    assert old["fields"]["aum"]["status"] == "accepted"
    assert uti_book_v4.derive(fund, bridges, details, [extraction], "2026-09-30") == old


@pytest.mark.parametrize("defect", ["wrong_aum_date", "wrong_portfolio_date", "no_row", "identity_conflict", "no_geometry", "wrong_geometry"])
def test_missing_dates_identity_or_region_do_not_create_aum(defect):
    extraction, fund, bridges, details = setup_derive()
    if defect == "wrong_aum_date":
        details[0]["left"] = details[0]["left"].replace("30th September", "31st August")
    elif defect == "wrong_portfolio_date":
        details[0]["page"]["text"] = details[0]["page"]["text"].replace("30th September", "31st August")
    elif defect == "no_row":
        details[0]["page"]["physical_aum_row"] = None
    elif defect == "identity_conflict":
        fund["plan"] = "DIRECT"
    elif defect == "no_geometry":
        details[0]["page"]["physical_geometry"] = None
    else:
        details[0]["page"]["physical_geometry"] = {**GEOMETRY, "boundary_x": 202}
    if defect in {"no_geometry", "wrong_geometry"}:
        with pytest.raises(ValueError, match="region"):
            uti_book_v4.derive(fund, bridges, details, [extraction], "2026-09-30")
    else:
        result = uti_book_v4.derive(fund, bridges, details, [extraction], "2026-09-30")
        assert result["fields"]["aum"]["status"] == "unavailable"


def real_fixture():
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    from test_uti_book_pdf_v2 import real_fixture as base

    reader = PdfReader(io.BytesIO(base(fractional=True)))
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    page = writer.pages[0]
    # StandardEncoding maps byte0x60 to a left quote. The issuer uses an
    # explicit backtick-as-INR glyph; this synthetic font must actually emit it.
    page["/Resources"]["/Font"]["/F1"][NameObject("/Encoding")] = NameObject("/WinAnsiEncoding")
    commands = []
    for x, y, em, text in spans():
        commands.append(f"BT /F1 {em} Tf {x} {y} Td ({text}) Tj ET")
    stream = DecodedStreamObject()
    stream.set_data(page.get_contents().get_data() + b"\n" + "\n".join(commands).encode())
    page.replace_contents(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_real_isolated_worker_preserves_every_frozen_column_and_header_field():
    original = real_fixture()
    old, new = uti_book_pdf_v2.extract(original), uti_book_pdf_v3.extract(original)
    assert new["source_hash"] == old["source_hash"] == hashlib.sha256(original).hexdigest()
    page = new["pages"][0]
    assert {k: v for k, v in page.items() if k != "physical_aum_row"} == old["pages"][0]
    expected = row.select([(x, y, float(em), text) for x, y, em, text in spans()], GEOMETRY | {"boundary_x": 204.0})
    assert page["physical_aum_row"] == expected
    for key in ("worker_wall_seconds", "worker_cpu_seconds", "linux_worker_address_space_bytes", "maximum_pages",
                "maximum_source_bytes", "maximum_page_stream_bytes", "maximum_total_stream_bytes", "maximum_geometry_output_bytes"):
        assert uti_book_pdf_v3.policy()[key] == uti_book_pdf_v2.policy()[key]
