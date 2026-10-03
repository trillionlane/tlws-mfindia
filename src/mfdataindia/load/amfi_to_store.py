"""Map parsed AMFI ``NAVAll.txt`` records onto ``mf.*`` rows.

The parser (:mod:`mfdataindia.ingest.amfi_navall`) is deliberately storage-agnostic
and stdlib-only so it can run unchanged on an India server. This module is the
only place that knows about PostgreSQL column order.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date
from typing import Any, Iterable, Iterator, Optional, Sequence

from mfdataindia.ingest.amfi_navall import AmfiScheme
from mfdataindia.load.normalise import (
    base_scheme_key,
    fold,
    normalise_amc,
    to_decimal_nav,
)
from mfdataindia.store.postgres import FUND_COLUMNS, LoadResult

__all__ = [
    "SCHEME_TYPE_MAP",
    "PERIODICITY_MAP",
    "UNKNOWN_CATEGORY",
    "map_scheme_type",
    "map_periodicity",
    "amc_names",
    "fund_rows",
    "nav_rows",
    "variant_rows",
    "load_variants",
    "load_parsed_amfi",
]

#: AMFI section-header wording -> mf.funds.scheme_type CHECK value.
SCHEME_TYPE_MAP: dict[str, str] = {
    "OPEN ENDED SCHEMES": "OPEN_ENDED",
    "CLOSE ENDED SCHEMES": "CLOSE_ENDED",
    "CLOSED ENDED SCHEMES": "CLOSE_ENDED",
    "INTERVAL FUND SCHEMES": "INTERVAL",
    "INTERVAL SCHEMES": "INTERVAL",
    # tolerate already-normalised input
    "OPEN_ENDED": "OPEN_ENDED",
    "CLOSE_ENDED": "CLOSE_ENDED",
    "INTERVAL": "INTERVAL",
}

#: Category written when a source row carries none. A real category is always
#: preferred; this only stops a NOT NULL violation from discarding the row.
UNKNOWN_CATEGORY = "UNCLASSIFIED"

#: Parser periodicity wording -> mf.funds.periodicity CHECK value.
#:
#: The parser matches AMFI's own spelling, which includes "HALF YEARLY" and
#: "HALF-YEARLY"; the schema stores the underscored enum form. Without this map a
#: valid AMFI row trips funds_periodicity_check and aborts the whole load.
PERIODICITY_MAP: dict[str, str] = {
    "DAILY": "DAILY",
    "WEEKLY": "WEEKLY",
    "FORTNIGHTLY": "FORTNIGHTLY",
    "MONTHLY": "MONTHLY",
    "QUARTERLY": "QUARTERLY",
    "HALF YEARLY": "HALF_YEARLY",
    "HALF-YEARLY": "HALF_YEARLY",
    "HALF_YEARLY": "HALF_YEARLY",
    "ANNUAL": "ANNUAL",
    "PERIODIC": "PERIODIC",
}


def map_periodicity(raw: Optional[str]) -> Optional[str]:
    """Map parsed periodicity wording to the schema's constrained value."""
    if not raw:
        return None
    key = str(raw).strip().upper()
    mapped = PERIODICITY_MAP.get(key)
    if mapped:
        return mapped
    # Defensive normalisation for wording the map does not yet cover.
    folded = key.replace("-", " ").replace("_", " ")
    folded = " ".join(folded.split()).replace(" ", "_")
    return PERIODICITY_MAP.get(folded.replace("_", " "), folded)


#: mf.funds enforces a 12-character ISIN. AMFI's file is mostly conformant but
#: carries a handful of status markers and truncated codes (11 rows in the
#: 27-Dec-2024 snapshot), so the loader must coerce rather than abort the load.
_ISIN_RE = re.compile(r"^[A-Z][A-Z0-9]{11}$")

#: Text AMFI writes in an ISIN column when a close-ended scheme has completed its
#: tenure and units were redeemed. This is a lifecycle signal, not an identifier.
REDEEMED_MARKER = "REDEEMED"


def clean_isin(raw: Optional[str]) -> Optional[str]:
    """Return a schema-valid ISIN, or None for markers and malformed codes.

    Never raises: an unusable ISIN must not discard an otherwise good scheme row.
    The rejected value is recoverable via :func:`isin_issue_rows`, which reports
    it into mf.quality_flags so the signal is not silently lost.
    """
    if not raw:
        return None
    value = str(raw).strip().upper()
    if not value:
        return None
    return value if _ISIN_RE.match(value) else None


def isin_marker(raw: Optional[str]) -> Optional[str]:
    """Return ``'REDEEMED'`` when AMFI marked the column as redeemed."""
    if not raw:
        return None
    return REDEEMED_MARKER if str(raw).strip().upper() == REDEEMED_MARKER else None


def is_redeemed(s: AmfiScheme) -> bool:
    """True when AMFI marked either ISIN column as redeemed (tenure completed)."""
    return bool(isin_marker(s.isin_div_payout_or_growth) or isin_marker(s.isin_div_reinvestment))


def map_scheme_type(raw: Optional[str]) -> str:
    """Map AMFI's scheme-type wording to the schema's constrained value."""
    key = fold(raw)
    mapped = SCHEME_TYPE_MAP.get(key)
    if mapped:
        return mapped
    # Fall back on a substring test: AMFI has varied the wording over the years.
    if "INTERVAL" in key:
        return "INTERVAL"
    if "CLOSE" in key:
        return "CLOSE_ENDED"
    return "OPEN_ENDED"


def amc_names(schemes: Iterable[AmfiScheme]) -> list[str]:
    """Distinct, non-empty AMC header strings, sorted for deterministic loads."""
    return sorted({s.amc.strip() for s in schemes if s.amc and s.amc.strip()})


def _code(s: AmfiScheme) -> Optional[int]:
    """AMFI scheme codes are numeric; return None rather than guess on junk."""
    raw = str(s.amfi_scheme_code or "").strip()
    if not raw.isdigit():
        return None
    return int(raw)


def fund_rows(
    schemes: Iterable[AmfiScheme],
    *,
    source_date: Optional[date] = None,
    category_source: str = "AMFI",
    metadata_authority: str = "AMFI",
) -> Iterator[tuple]:
    """Yield rows in :data:`FUND_COLUMNS` order.

    Rows with a non-numeric scheme code are skipped: they cannot be a primary key
    and should already have been quarantined by the parser.
    """
    for s in schemes:
        code = _code(s)
        if code is None:
            continue
        category = (s.scheme_category or "").strip() or UNKNOWN_CATEGORY
        yield (
            code,                                   # amfi_scheme_code
            None,                                   # mfapi_scheme_code
            s.scheme_name,                          # scheme_name
            fold(s.scheme_name),                    # scheme_name_norm
            (s.amc or "").strip() or None,          # amfi_amc_name
            map_scheme_type(s.scheme_type),         # scheme_type
            category,                               # scheme_category
            s.scheme_category,                      # scheme_category_raw
            category_source,                        # category_source
            s.plan_type,                            # plan_type
            s.plan_source,                          # plan_source
            s.option,                               # option_type
            map_periodicity(s.periodicity),         # periodicity
            bool(s.is_etf),                         # is_etf
            bool(s.is_defunct),                     # is_defunct
            bool(s.nav_not_published),              # nav_not_published
            clean_isin(s.isin_div_payout_or_growth),  # isin_growth_or_div_payout
            clean_isin(s.isin_div_reinvestment),      # isin_div_reinvest
            source_date,                            # first_seen_in_source
            source_date,                            # last_seen_in_source
            # A redeemed close-ended scheme has completed its tenure: it is not
            # active, but it is also not "defunct" (which implies it failed or was
            # merged). Both suppress is_active; only defunct sets is_defunct.
            not (bool(s.is_defunct) or is_redeemed(s)),  # is_active
            metadata_authority,                     # metadata_authority
        )


def nav_rows(
    schemes: Iterable[AmfiScheme],
    *,
    source: str = "AMFI",
    cross_verified: bool = False,
) -> Iterator[tuple]:
    """Yield ``(amfi_scheme_code, nav_date, nav, source, is_cross_verified)``.

    Rows without a NAV or a date are skipped: AMFI marks long-dead schemes with
    ``N.A.``, which the parser records as ``nav_not_published`` rather than as a
    NAV of zero. Writing zero would fabricate a price that was never published.
    """
    for s in schemes:
        code = _code(s)
        if code is None or s.nav_date is None:
            continue
        nav = to_decimal_nav(s.nav)
        if nav is None:
            continue
        yield (code, s.nav_date, nav, source, bool(cross_verified))


#: Columns of mf.quality_flags written by :func:`quality_flag_rows`.
FLAG_COLUMNS = (
    "amfi_scheme_code",
    "nav_date",
    "flag_type",
    "severity",
    "message",
    "details",
    "source",
)


def quality_flag_rows(
    schemes: Iterable[AmfiScheme],
    *,
    source: str = "AMFI",
    include_defunct: bool = True,
) -> Iterator[tuple]:
    """Yield mf.quality_flags rows for anomalies found in parsed AMFI data.

    This is where coerced values are reported rather than silently dropped: an
    ISIN that could not be stored, or a scheme AMFI marked redeemed, still shows
    up here with its raw source value in ``details``.
    """
    for s in schemes:
        code = _code(s)
        if code is None:
            continue

        for column, raw in (
            ("A", s.isin_div_payout_or_growth),
            ("B", s.isin_div_reinvestment),
        ):
            if not raw or not str(raw).strip():
                continue
            value = str(raw).strip().upper()
            if _ISIN_RE.match(value):
                continue
            if value == REDEEMED_MARKER:
                yield (
                    code, s.nav_date, "LIFECYCLE_ENDED", "INFO",
                    "AMFI marked the ISIN column REDEEMED: close-ended scheme "
                    "completed its tenure and units were redeemed",
                    json.dumps({
                        "isin_column": column,
                        "raw_value": value,
                        "scheme_name": s.scheme_name,
                        "scheme_type": s.scheme_type,
                    }),
                    source,
                )
            else:
                yield (
                    code, s.nav_date, "ISIN_MISSING", "WARN",
                    f"ISIN column {column} holds a non-conforming value "
                    f"(expected 12 chars); stored as NULL",
                    json.dumps({
                        "isin_column": column,
                        "raw_value": value,
                        "length": len(value),
                        "scheme_name": s.scheme_name,
                    }),
                    source,
                )

        if include_defunct and s.is_defunct:
            yield (
                code, s.nav_date, "DEAD_SCHEME", "INFO",
                "Scheme flagged defunct by the parser (NAV 0 / N.A. / defunct naming)",
                json.dumps({
                    "scheme_name": s.scheme_name,
                    "nav": s.nav,
                    "nav_not_published": bool(s.nav_not_published),
                }),
                source,
            )

        if getattr(s, "plan_source", None) == "COLUMN_BLANK":
            yield (
                code, s.nav_date, "NAME_PARSE_FAILURE", "INFO",
                "AMFI feed left the Plan column blank; plan is unknown, so the "
                "scheme is stored with plan_type=UNLABELLED and excluded from the "
                "default Regular-Plan scope",
                json.dumps({
                    "scheme_name": s.scheme_name,
                    "plan_type": s.plan_type,
                    "plan_source": "COLUMN_BLANK",
                }),
                source,
            )


def variant_rows(schemes: Iterable[AmfiScheme]) -> Iterator[tuple]:
    """Yield mf.fund_variants rows, resolving Regular<->Direct pairs.

    A pair is only accepted when both variants share the same AMC and scheme
    type. The base-scheme key alone is a heuristic and can collide, so requiring
    AMC and type agreement is what makes the pairing trustworthy.
    """
    buckets: dict[tuple[str, str, str], dict[str, list[int]]] = defaultdict(
        lambda: {"REGULAR": [], "DIRECT": []}
    )
    names: dict[int, str] = {}

    for s in schemes:
        code = _code(s)
        if code is None:
            continue
        gkey = base_scheme_key(s.scheme_name)
        if not gkey:
            continue
        names[code] = s.scheme_name
        key = (normalise_amc(s.amc), map_scheme_type(s.scheme_type), gkey)
        if s.plan_type in ("REGULAR", "DIRECT"):
            buckets[key][s.plan_type].append(code)

    for (_amc, _stype, gkey), bucket in buckets.items():
        regulars = bucket["REGULAR"]
        directs = bucket["DIRECT"]
        # Representative codes make the group navigable even where a full 1:1
        # option-level pairing is not possible from names alone.
        reg_rep = regulars[0] if regulars else None
        dir_rep = directs[0] if directs else None
        for code in sorted(set(regulars) | set(directs)):
            yield (code, gkey, names[code], reg_rep, dir_rep, bool(regulars and directs))


_STG_VARIANTS_DDL = """
    amfi_scheme_code   integer NOT NULL,
    group_key          text    NOT NULL,
    base_scheme_name   text    NOT NULL,
    regular_code       integer,
    direct_code        integer,
    has_direct_sibling boolean NOT NULL DEFAULT false
"""

_VARIANT_COLS = (
    "amfi_scheme_code",
    "group_key",
    "base_scheme_name",
    "regular_code",
    "direct_code",
    "has_direct_sibling",
)


def load_variants(store: Any, schemes: Sequence[AmfiScheme]) -> LoadResult:
    """Upsert mf.fund_variants from parsed schemes."""
    res = LoadResult(target="fund_variants")
    rows = list(variant_rows(schemes))
    res.staged = len(rows)
    if not rows:
        return res

    with store.transaction() as conn, conn.cursor() as cur:
        store._create_staging(cur, "stg_variants", _STG_VARIANTS_DDL)
        store._copy_rows(cur, "stg_variants", _VARIANT_COLS, iter(rows))
        before = store._count(cur, "fund_variants")
        cur.execute(
            """
            INSERT INTO mf.fund_variants AS v (
                amfi_scheme_code, group_key, base_scheme_name,
                regular_code, direct_code, has_direct_sibling, updated_at
            )
            SELECT s.amfi_scheme_code, s.group_key, s.base_scheme_name,
                   s.regular_code, s.direct_code, s.has_direct_sibling, now()
            FROM (
                SELECT DISTINCT ON (amfi_scheme_code) *
                FROM mf.stg_variants ORDER BY amfi_scheme_code
            ) s
            ON CONFLICT (amfi_scheme_code) DO UPDATE SET
                group_key          = EXCLUDED.group_key,
                base_scheme_name   = EXCLUDED.base_scheme_name,
                regular_code       = EXCLUDED.regular_code,
                direct_code        = EXCLUDED.direct_code,
                has_direct_sibling = EXCLUDED.has_direct_sibling,
                updated_at         = now()
            WHERE v.group_key          IS DISTINCT FROM EXCLUDED.group_key
               OR v.regular_code       IS DISTINCT FROM EXCLUDED.regular_code
               OR v.direct_code        IS DISTINCT FROM EXCLUDED.direct_code
               OR v.has_direct_sibling IS DISTINCT FROM EXCLUDED.has_direct_sibling
            """
        )
        written = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        res.inserted = store._count(cur, "fund_variants") - before
        res.updated = max(0, written - res.inserted)
        res.unchanged = max(0, res.staged - written)
        store._drop(cur, "stg_variants")
    return res


def load_parsed_amfi(
    store: Any,
    schemes: Sequence[AmfiScheme],
    *,
    source_date: Optional[date] = None,
    include_nav: bool = True,
    include_variants: bool = True,
    include_flags: bool = True,
) -> dict[str, Any]:
    """Load parsed AMFI schemes into a :class:`PostgresStore`, in dependency order.

    AMCs first (funds carry a NOT NULL FK), then funds, then NAV, then variants,
    then quality flags. Each step is its own transaction, so a later failure
    leaves earlier steps committed and the run can be resumed.
    """
    results: dict[str, Any] = {}
    results["amcs"] = store.load_amcs(amc_names(schemes)).as_dict()
    results["funds"] = store.load_funds(
        fund_rows(schemes, source_date=source_date)
    ).as_dict()
    if include_nav:
        results["nav"] = store.upsert_nav(nav_rows(schemes)).as_dict()
    if include_variants:
        results["variants"] = load_variants(store, schemes).as_dict()
    if include_flags:
        results["flags"] = store.record_flags(
            quality_flag_rows(schemes), replace_source="AMFI"
        ).as_dict()
    return results
