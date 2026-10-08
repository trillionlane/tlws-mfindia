"""AMC factsheet parsers: layout-agnostic core (Option A enrichment source).

The AMC's monthly factsheet PDF is the *official* source of record for
scheme-level metrics that the other providers leave NULL: beta, Sharpe,
standard deviation (and alpha where an AMC publishes it). This module is the
parser half of the pipeline:

    PDF text  ->  SchemeFactsheet dataclasses   (this package, pure)
    dataclasses -> fund_facts fill-if-missing   (mfdataindia.load)

Design rules
------------
* **One parser class per AMC** (``AmcFactsheetParser``). AMC factsheet layouts
  differ; a new AMC = a new subclass, never a rewrite. The base types here
  are the contract every parser produces.
* **Parsing is pure** — page text in, dataclasses out, no I/O, no DB, so
  each parser is unit-testable against saved page fixtures.
* **NA is a value, not a failure.** AMCs legitimately publish ``NA`` for
  metrics they don't compute (e.g. ABSL publishes NA beta/Sharpe/std-dev for
  debt and money-market funds). Parsers normalise both *published NA* and
  *absent* to ``None``: the action is identical (do not fill), and the
  loader logs the field either way so coverage reports are honest.
* **Name matching is exact-or-AND-normalised with an ambiguity guard.**
  AMFI names use "&" while AMC PDFs usually spell "and"; both match after
  AND-stripping. If two distinct schemes collide on the normalised key the
  match is refused, never guessed.

Units follow the ``mf.fund_facts`` columns: ``beta`` and ``sharpe_ratio``
as ratios, ``std_deviation`` as a percent, returns as percentages.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Iterator, Optional

from mfdataindia.load.normalise import fold

__all__ = [
    "PerformanceRow",
    "QuantMetrics",
    "SchemeFactsheet",
    "AmcFactsheetParser",
    "NameMatcher",
    "NoMatchError",
]

#: One metric token: an optional sign, digits, optional decimal, optional %.
_NUM = r"-?[0-9]+(?:\.[0-9]+)?%?"

#: Tokens AMCs use for "not published / not applicable".
_NA_TOKENS = ("NA", "N.A.", "N/A", "-", "")


@dataclass(slots=True)
class PerformanceRow:
    """One row of an 'Investment Performance' returns table (CAGR, %).

    ``role``: ``scheme`` | ``benchmark`` | ``additional_benchmark``
    (and the same with a ``_sip`` suffix where the factsheet separates
    SIP CAGR tables — parsers that don't see them simply don't emit them).
    """

    role: str
    name: str
    since_inception: Optional[float] = None
    return_10y: Optional[float] = None
    return_5y: Optional[float] = None
    return_3y: Optional[float] = None
    return_1y: Optional[float] = None


@dataclass(slots=True)
class QuantMetrics:
    """The AMC-published 'Quantitative Measures' block.

    ``None`` means "not fillable from this source" — either absent or
    published NA. ``alpha`` is reserved: no AMC layout inspected to date
    publishes it, but the slot keeps the contract stable for ones that do.
    """

    std_deviation_pct: Optional[float] = None
    beta: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    alpha: Optional[float] = None
    tracking_error_1y: Optional[float] = None
    tracking_error_3y: Optional[float] = None
    information_ratio: Optional[float] = None


@dataclass(slots=True)
class SchemeFactsheet:
    """Everything one factsheet says about one base scheme."""

    amc: str
    scheme_name: str
    factsheet_period: str            # document month, e.g. "September 2026"
    page: int                        # 1-based page of the detail page
    quant: QuantMetrics = field(default_factory=QuantMetrics)
    performance: list[PerformanceRow] = field(default_factory=list)
    benchmark_name: Optional[str] = None
    additional_benchmark_name: Optional[str] = None
    inception_date: Optional[date] = None
    nav_date: Optional[date] = None  # "NAV as on <date>"
    nav_growth_regular: Optional[float] = None
    aum_crore: Optional[float] = None
    expense_ratio_regular: Optional[float] = None
    expense_ratio_direct: Optional[float] = None
    fund_manager: Optional[str] = None
    fund_category: Optional[str] = None
    raw_detail: str = ""             # detail-page text; for audits/repairs


class AmcFactsheetParser(ABC):
    """Contract every AMC parser implements.

    ``parse_pages`` receives the full page-text list of ONE factsheet PDF
    (already extracted, in order) and yields one ``SchemeFactsheet`` per
    base scheme. Parsers must be idempotent and side-effect free.
    """

    #: Short machine name, e.g. ``"absl"`` — used in logs and provenance.
    amc: str = "generic"

    @abstractmethod
    def parse_pages(self, pages: Iterable[str]) -> Iterator[SchemeFactsheet]:
        ...


def to_num(v: Optional[str]) -> Optional[float]:
    """Parse a metric value; NA tokens / empty -> None."""
    if v is None:
        return None
    v = v.strip().rstrip("%").strip()
    if v.upper() in _NA_TOKENS:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _strip_and(text: str) -> str:
    """``fold`` with standalone ANDs removed, so 'Banking & PSU' and
    'Banking and PSU' match. (fold already normalises spaces/case.)"""
    return re.sub(r"\sAND\s", " ", fold(text))


class NoMatchError(Exception):
    """A factsheet name matched no scheme at all."""


class NameMatcher:
    """Match factsheet scheme names to ``mf.funds`` scheme codes.

    Built once per run from the candidate set (in-scope live funds — the
    loader passes only Regular Growth variant codes, because factsheet
    metrics are computed on the Regular Growth NAV). Two keys per
    candidate: the exact ``fold`` and the AND-stripped variant (so an
    AMC's "Banking and PSU" matches AMFI's "Banking & PSU").

    Why this is unambiguous without a separate guard: ``fold`` removes
    ``&`` (it is a non-alphanumeric), so an ``&``-spelled candidate's
    exact key *is* its AND-stripped key. Any query whose AND-stripped key
    is shared by two candidates therefore either (a) hits one of them via
    its exact key — deterministic, spelling wins — or (b) contains "AND",
    in which case its exact key is the "and"-candidate's key, which again
    hits deterministically. A collision can never be reached by guesswork,
    so no ambiguous-match path exists (and none is written).

    The candidate set must contain unique scheme names (true within one
    AMC's in-scope Regular Growth variants); the CLI asserts this.
    """

    def __init__(self, candidates: dict[str, int]):
        # candidates: scheme_name -> amfi_scheme_code (already filtered)
        self._exact: dict[str, int] = {}
        and_hits: dict[str, set[int]] = {}
        for name, code in candidates.items():
            self._exact.setdefault(fold(name), code)
            and_hits.setdefault(_strip_and(name), set()).add(code)
        self._and = {
            k: next(iter(v)) for k, v in and_hits.items() if len(v) == 1
        }

    def match(self, factsheet_name: str) -> int:
        """Return the amfi_scheme_code, or raise NoMatchError."""
        code = self._exact.get(fold(factsheet_name))
        if code is not None:
            return code
        code = self._and.get(_strip_and(factsheet_name))
        if code is not None:
            return code
        raise NoMatchError(factsheet_name)