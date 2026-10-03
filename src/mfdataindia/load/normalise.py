"""Name normalisation, scheme grouping keys, and exact NAV conversion.

These helpers exist so that identity resolution is consistent between the AMFI
parser, the mfapi.in adapter, and the SQL schema. The plan/option vocabulary is
imported from :mod:`mfdataindia.ingest.amfi_navall` rather than duplicated, so
the Python classifier and ``mf.funds.in_scope`` can never disagree.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional

from mfdataindia.ingest.amfi_navall import _PERIODICITY

__all__ = [
    "fold",
    "normalise_amc",
    "base_scheme_key",
    "to_decimal_nav",
]

#: Quantisation target matching mf.nav_history.nav NUMERIC(18,4).
_NAV_EXP = Decimal("0.0001")

_FOLD_STRIP = re.compile(r"[^A-Z0-9]+")

#: Separators AMFI uses between the base scheme name and its plan/option tail.
_SEPARATORS = re.compile(r"\s*[-\u2013\u2014|/]\s*|\s*\(\s*|\s*\)\s*|\s*\.\s+")

#: A segment made *entirely* of plan/option/periodicity vocabulary. Matching the
#: whole segment is deliberate: stripping "GROWTH" anywhere in a name would
#: corrupt schemes whose theme genuinely contains the word (e.g. a "Long Term
#: Growth" fund). Only trailing variant segments are removed.
_VARIANT_ONLY = re.compile(
    r"^(?:(?:DIRECT|REGULAR|RETAIL|INSTITUTIONAL|PLAN|GROWTH|IDCW|DIVIDEND|DIV|"
    r"BONUS|CUMULATIVE|OPTION|PAYOUT|REINVESTMENT|REINVEST|INCOME|DISTRIBUTION|"
    r"CUM|CAPITAL|WITHDRAWAL|DIST|OF|AND|THE|DAILY|WEEKLY|FORTNIGHTLY|MONTHLY|"
    r"QUARTERLY|HALF|YEARLY|ANNUAL|PERIODIC)\s*)+$"
)

#: AMC name noise: the trailing legal-form suffix carries no discriminating value.
_AMC_SUFFIXES = ("MUTUAL FUND", "ASSET MANAGEMENT", "FUND")


def fold(text: Optional[str]) -> str:
    """Case-fold to a stable comparison key: ASCII, uppercase, single spaces.

    Diacritics are decomposed and stripped because AMC/scheme names mix Latin-1
    and Unicode punctuation across sources.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _FOLD_STRIP.sub(" ", ascii_only.upper()).strip()


def normalise_amc(name: Optional[str]) -> str:
    """Normalise an AMC name for cross-source matching.

    ``"SBI Mutual Fund"`` and ``"SBI"`` fold to the same key, which is what lets
    AMFI, mfapi.in and Scripbox AMC spellings join.
    """
    folded = fold(name)
    for suffix in _AMC_SUFFIXES:
        if folded.endswith(suffix) and len(folded) > len(suffix) + 1:
            folded = folded[: -len(suffix)].strip()
            break
    return folded


def _is_variant_segment(segment: str) -> bool:
    return bool(_VARIANT_ONLY.match(fold(segment)))


def base_scheme_key(name: Optional[str]) -> str:
    """Derive the base-scheme grouping key used by mf.fund_variants.group_key.

    Drops trailing plan/option segments so that ``"... Multi Asset Fund - Regular
    Plan - Growth"`` and ``"... Multi Asset Fund - Direct Plan - IDCW Option"``
    collapse onto one key. The first segment is never dropped, so a name that is
    nothing but variant tokens still yields something usable.

    This is a heuristic. Callers must confirm a Regular<->Direct pairing by also
    requiring the same AMC and scheme category; the key alone can collide.
    """
    if not name:
        return ""
    segments = [s for s in _SEPARATORS.split(str(name)) if s and s.strip()]
    while len(segments) > 1 and _is_variant_segment(segments[-1]):
        segments.pop()
    return fold(" ".join(segments))


def to_decimal_nav(value: Any) -> Optional[Decimal]:
    """Convert a parsed NAV to an exact ``Decimal`` quantised to 4 dp.

    ``mf.nav_history.nav`` is NUMERIC(18,4). Passing a float to psycopg would map
    to DOUBLE PRECISION and reintroduce binary representation error into financial
    data, so the parser's float is routed through ``str()`` — which for the <=4
    decimal values AMFI publishes round-trips exactly — and then quantised.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, float):
        dec = Decimal(str(value))
    elif isinstance(value, int):
        dec = Decimal(value)
    else:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            dec = Decimal(text)
        except InvalidOperation:
            return None
    if not dec.is_finite():
        return None
    return dec.quantize(_NAV_EXP, rounding=ROUND_HALF_UP)


# _PERIODICITY is re-exported implicitly for callers that need the vocabulary;
# referenced here to keep the import meaningful and lint-clean.
PERIODICITY_TOKENS: tuple[str, ...] = _PERIODICITY
