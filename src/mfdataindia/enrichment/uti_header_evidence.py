"""Versioned sealed header geometry; never financial-value coercion.

The frozen decoder's measurements are retained as exact plain decimal strings
from their existing representation. No further rounding or numeric-ledger waiver.
"""
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
from pathlib import Path

FIELDS = ("x", "y", "font_points", "page_width", "page_height")
KEYS = {"method", "source_hash", "text", "text_hash", *FIELDS}


def policy():
    return {"id": "uti-physical-header-sealed-geometry", "version": 1,
            "encoding": "plain exact decimal strings for x/y/font_points/page_width/page_height; no exponents, nonfinite values, booleans or extra keys",
            "precision": "Decimal(str(frozen_decoder_measurement)), fixed notation, no new rounding; normalize only signed numeric zero",
            "coordinate_unit": "PDF_points", "global_canonicalizer_changed": False,
            "predicate_context": {"precision": 50, "rounding": "ROUND_HALF_EVEN"},
            "financial_values_changed": False,
            "engine_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def encode(header):
    if not isinstance(header, dict) or set(header) != KEYS:
        raise ValueError("exact original header evidence required")
    values = {}
    for field in FIELDS:
        value = header[field]
        if type(value) not in (int, float):
            raise ValueError("finite decoder geometry required, never arbitrary coercion")
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("finite decoder geometry required, never arbitrary coercion")
        token = "0" if number == 0 else format(number, "f")
        # Frozen worker measurements are bounded PDF coordinates, not arbitrary
        # high precision input. Encoding may never widen those physical bounds.
        import re
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,17})?", token):
            raise ValueError("bounded exact decoder decimal representation required")
        values[field] = token
    width, height = Decimal(values["page_width"]), Decimal(values["page_height"])
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        if (not 0 < width <= 2000 or not 0 < height <= 2000
                or not 0 <= Decimal(values["x"]) < width / 10
                or not height * Decimal("0.9") <= Decimal(values["y"]) <= height
                or not 8 <= Decimal(values["font_points"]) <= 30):
            raise ValueError("original header geometry outside frozen physical region")
    return {**header, **values}
