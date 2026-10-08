"""Load AMC-factsheet metrics into mf.fund_facts — fill-if-missing only.

The factsheet is an *official* source (the AMC's own published figures),
not a computed one, so provenance is recorded in
``mf.factsheet_fields_log`` (per fund/field/period) and the fetch itself
is recorded via ``store.record_fetch`` with source='AMC'
(requires migration 011).

Invariant — identical to the computed-metrics fill:

* Only **NULL** target fields are written, via
  ``SET col = COALESCE(col, %s)``. A non-NULL value can never be
  overwritten, whatever the factsheet says.
* Only the **Regular Growth** variant code of a matched base scheme is
  written: ABSL states its performance/quantitative figures are for the
  Regular Plan - Growth Option, and the IDCW variant has a different NAV
  (post-distribution) so the figures would not describe it.
* Funds whose factsheet name does not match a candidate are skipped and
  counted — never guessed.

Fields filled (v1, per the project decision): ``beta``, ``sharpe_ratio``,
``std_deviation``. ``alpha`` is in the parse contract but **not fillable
today** — no AMC layout inspected to date publishes it (recorded as a
finding, not silently dropped).
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from mfdataindia.ingest.amc_factsheets.base import (
    NameMatcher,
    NoMatchError,
    SchemeFactsheet,
)
from mfdataindia.store.postgres import PostgresStore

log = logging.getLogger("amc_factsheets")

#: quant attribute -> fund_facts column. This whitelist is the ONLY set of
#: fields the loader may write; extending it is a code change, not a flag.
FILLABLE: dict[str, str] = {
    "beta": "beta",
    "sharpe_ratio": "sharpe_ratio",
    "std_deviation_pct": "std_deviation",
}

_SOURCE = "AMC"


def load_factsheets(
    store: PostgresStore,
    factsheets: Iterable[SchemeFactsheet],
    candidates: dict[str, int],
    *,
    factsheet_url: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fill NULL target fields from parsed factsheets.

    :param store: connected store (transactional writes).
    :param factsheets: parsed ``SchemeFactsheet`` objects.
    :param candidates: ``scheme_name -> amfi_scheme_code`` — in-scope live
        Regular Growth variants only (the loader is variant-agnostic).
    :param factsheet_url: source URL, for provenance.
    :param dry_run: report what would be written; write nothing.
    """
    matcher = NameMatcher(candidates)
    rep: dict[str, Any] = {
        "factsheets": 0, "matched": 0, "no_match": 0,
        "funds_filled": 0, "funds_no_gaps": 0, "funds_all_na": 0,
        "fills_by_field": {}, "dry_run": dry_run,
    }
    for fs in factsheets:
        rep["factsheets"] += 1
        try:
            code = matcher.match(fs.scheme_name)
        except NoMatchError:
            rep["no_match"] += 1
            continue
        rep["matched"] += 1

        with store.connect().cursor() as cur:
            current = cur.execute(
                "SELECT beta, sharpe_ratio, std_deviation "
                "FROM mf.fund_facts WHERE amfi_scheme_code = %s", (code,)).fetchone()

        to_fill: dict[str, float] = {}
        for attr, col in FILLABLE.items():
            value = getattr(fs.quant, attr)
            if value is None:
                continue
            existing = current[col] if current is not None else None
            if existing is None:
                to_fill[col] = value

        if not to_fill:
            # Either the fund already had everything, or the factsheet
            # published NA for all fillable fields — both are "nothing to do",
            # but the report distinguishes them for coverage questions.
            if current is not None and all(
                    current[c] is not None for c in FILLABLE.values()):
                rep["funds_no_gaps"] += 1
            else:
                rep["funds_all_na"] += 1
            continue

        rep["funds_filled"] += 1
        for col in to_fill:
            rep["fills_by_field"][col] = rep["fills_by_field"].get(col, 0) + 1

        if dry_run:
            continue
        with store.transaction() as conn, conn.cursor() as cur:
            _fill_fund(cur, code, fs, to_fill, factsheet_url)

    log.info("amc_factsheets: %s", rep)
    return rep


def _fill_fund(cur, code: int, fs: SchemeFactsheet,
               to_fill: dict[str, float], factsheet_url: Optional[str]) -> None:
    """Fill-if-missing UPDATE + provenance rows for one fund."""
    cur.execute(
        f"""
        UPDATE mf.fund_facts
        SET {', '.join(f'{c} = COALESCE({c}, %s)' for c in to_fill)}
        WHERE amfi_scheme_code = %s
        """,
        list(to_fill.values()) + [code])
    for col, value in to_fill.items():
        cur.execute(
            """
            INSERT INTO mf.factsheet_fields_log
                (amfi_scheme_code, field, value, amc, factsheet_period,
                 as_of, pdf_page, doc_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (amfi_scheme_code, field, factsheet_period) DO UPDATE SET
                value = EXCLUDED.value, as_of = EXCLUDED.as_of,
                pdf_page = EXCLUDED.pdf_page, doc_url = EXCLUDED.doc_url,
                extracted_at = now()
            """,
            (code, col, value, fs.amc, fs.factsheet_period,
             fs.nav_date, fs.page, factsheet_url))