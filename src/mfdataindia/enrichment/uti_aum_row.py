"""Independent complete physical Closing-AUM row; never AAUM or text adjacency.

This adds a new evidence rule. It does not change the frozen column grouping,
title decoder, date parser, old financial facts or global ledger canonicalizer.
"""

from decimal import Context, Decimal, localcontext
import math
import re

NUMBER = r"(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,17})?"
AMOUNT = r"(?:[1-9][0-9]{0,2}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,6})?"


def policy():
    return {"id": "uti-complete-physical-closing-aum-row", "version": 1,
            "scope": "only an explicit Closing AUM row inside the frozen same-fund physical left column; original label, colon, INR glyph, positive amount and Crore unit in that exact left-to-right order",
            "baseline_delta": "at most one quarter of label font em for EVERY original span; a new independent row predicate, not a change to frozen 0.75-point column grouping",
            "ambiguity": "exactly one Closing AUM label in the entire selected column; exactly five original spans for separate label/colon or four with original combined Closing AUM : span, never synthetic splitting; duplicates, extra text, wrong units/currency, rotated/multiline spans and partial rows unavailable",
            "coordinate_encoding": "bounded exact plain decimal strings from original finite PDF measurements; no extra rounding",
            "date": "unchanged explicit Fund AUM/Folio as on equals same-fund Portfolio as on month-end",
            "accepted_old_values": "never replace; new row may only fill previously unavailable AUM",
            "financial_basis": "Closing AUM INR crore; never monthly average AUM, BER, estimated or adjusted NAV"}


def token(number):
    if type(number) not in (int, float) or not math.isfinite(number):
        raise ValueError("finite original physical measurement required")
    value = Decimal(str(number))
    result = "0" if value == 0 else format(value, "f")
    if not re.fullmatch(NUMBER, result):
        raise ValueError("bounded exact physical decimal required")
    return result


def validate(row):
    """Revalidate worker evidence before use and before ledger serialization."""
    if not isinstance(row, dict) or set(row) != {"method", "page_width", "page_height", "boundary_x", "spans"}:
        raise ValueError("exact complete physical AUM evidence required")
    if row["method"] != "complete_original_closing_aum_row" or not isinstance(row["spans"], list) or len(row["spans"]) not in (4, 5):
        raise ValueError("four or five complete original AUM spans required")
    dimensions = ("page_width", "page_height", "boundary_x")
    spans = row["spans"]
    for span in spans:
        if not isinstance(span, dict) or set(span) != {"text", "x", "y", "font_points"} or not isinstance(span["text"], str):
            raise ValueError("exact original AUM span required")
    for obj, keys in [(row, dimensions), *((span, ("x", "y", "font_points")) for span in spans)]:
        if any(type(obj[k]) is not str or not re.fullmatch(NUMBER, obj[k]) for k in keys):
            raise ValueError("canonical bounded original AUM coordinates required")
    labels = [s["text"] for s in spans[:-3]]
    if labels not in (["Closing AUM", ":"], ["Closing AUM :"]) or spans[-3]["text"] not in ("`", "₹"):
        raise ValueError("explicit Closing AUM label colon and INR glyph required")
    if spans[-1]["text"] != "Crore" or not re.fullmatch(AMOUNT, spans[-2]["text"]):
        raise ValueError("explicit original Crore amount required")
    with localcontext(Context(prec=50)):
        width, height, boundary = (Decimal(row[k]) for k in dimensions)
        if not 0 < width <= 2000 or not 0 < height <= 2000 or not width / 5 < boundary < width / 2:
            raise ValueError("bounded original left field region required")
        if not 0 < Decimal(spans[-2]["text"].replace(",", "")) <= Decimal("1000000000000"):
            raise ValueError("bounded positive Closing AUM required")
        label_y, label_em = Decimal(spans[0]["y"]), Decimal(spans[0]["font_points"])
        positions = [Decimal(s["x"]) for s in spans]
        if positions != sorted(set(positions)):
            raise ValueError("complete original AUM order required")
        for span in spans:
            if not (0 <= Decimal(span["x"]) < boundary
                    and 0 <= Decimal(span["y"]) <= height
                    and 4 <= Decimal(span["font_points"]) <= 16
                    and abs(Decimal(span["y"]) - label_y) <= label_em / 4):
                raise ValueError("original AUM span outside complete physical row")
    return row


def select(spans, geometry):
    """No nearest-number fallback: require one complete original physical row."""
    labels = [s for s in spans if s[3] in ("Closing AUM", "Closing AUM :")]
    if len(labels) != 1:
        return None
    label = labels[0]
    # Measurements are encoded before comparisons; decimal predicates do not
    # inherit ambient process precision or machine-dependent float subtraction.
    try:
        encoded = [{"x": token(x), "y": token(y), "font_points": token(em), "text": text}
                   for x, y, em, text in spans]
        encoded_label = next(s for s in encoded if s["text"] in ("Closing AUM", "Closing AUM :"))
        with localcontext(Context(prec=50)):
            delta = Decimal(token(label[2])) / 4
            selected = sorted((s for s in encoded if abs(Decimal(s["y"]) - Decimal(encoded_label["y"])) <= delta),
                              key=lambda s: Decimal(s["x"]))
        return validate({"method": "complete_original_closing_aum_row",
                         **{k: token(geometry[k]) for k in ("page_width", "page_height", "boundary_x")},
                         "spans": selected})
    except (ValueError, ArithmeticError):
        return None


def page_row(page, geometry, limits):
    """Read original unrotated spans in the ALREADY validated frozen column."""
    spans = []
    seen = 0

    def visit(text, cm, tm, font, size):
        nonlocal seen
        if not text.strip():
            return
        seen += 1
        if seen > limits["maximum_page_spans"]:
            raise ValueError("physical AUM span bound")
        if any(not math.isfinite(float(v)) for v in [*cm, *tm, size]):
            raise ValueError("finite original AUM transform required")
        if any(abs(v) > 0.0001 for v in (cm[1], cm[2], tm[1], tm[2])):
            return
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        em = abs(size) * max(abs(tm[0]), abs(tm[3])) * max(abs(cm[0]), abs(cm[3]))
        if 0 <= x < geometry["boundary_x"] and 0 <= y <= geometry["anchor_y"] + 0.75:
            spans.append((x, y, em, text.strip()))

    page.extract_text(visitor_text=visit)
    return select(spans, geometry)
