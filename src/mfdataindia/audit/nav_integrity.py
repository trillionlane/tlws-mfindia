"""NAV-integrity assessment: detection signals over the complete NAV history.

Signals (closed set) are *detection* signals — structural anomalies worth
human review — never proof that a stored NAV is wrong, and never a license to
rewrite history:

* ``constant_nav_series`` — one distinct NAV across the full history
  (>= :data:`CONSTANT_MIN_OBSERVATIONS` observations).
* ``duplicate_variant_series`` — two or more scheme codes inside ONE guarded
  family identity (AMC + scheme type + scheme category + ``fund_variants
  .group_key``) carrying exactly identical series. A candidate is reported
  only after a canonical fingerprint narrows it AND the full series compare
  exactly, value for value.
* ``terminal_face_value_reset_candidate`` — the terminal observation is
  exactly :data:`FACE_VALUE` (10.0000, the FMP redemption-at-face pattern)
  after a prior observation at or above :data:`TERMINAL_RESET_PREV_FLOOR`.

The scan is read-only over ``mf.nav_history`` (one aggregate pass plus
verification fetches for the small candidate set) and its only write target is
``mf.nav_quality_assessments`` (upsert per scheme; stale rows cleared).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional

METHODOLOGY_VERSION = "1"

SIGNAL_CONSTANT = "constant_nav_series"
SIGNAL_DUPLICATE = "duplicate_variant_series"
SIGNAL_TERMINAL_RESET = "terminal_face_value_reset_candidate"

#: A series needs this many observations before "one distinct value" stops
#: being a plausible young fund and becomes a structural anomaly.
CONSTANT_MIN_OBSERVATIONS = 250

#: SEBI FMP units are issued/redeemed at a 10.00 face value.
FACE_VALUE = Decimal("10.0000")
#: The prior observation must be meaningfully above face for the terminal
#: observation to read as a redemption reset rather than an early fund.
TERMINAL_RESET_PREV_FLOOR = Decimal("11.0000")

#: Full-history aggregate pass: one row per scheme with the exact series
#: fingerprint (md5 of the canonical date:value sequence). ``array_agg`` per
#: scheme stays bounded by the series length (~a few thousand rows max).
_AGG_SQL = """
    SELECT amfi_scheme_code,
           count(*)::bigint                                   AS observation_count,
           count(DISTINCT nav)::bigint                        AS distinct_nav_count,
           min(nav_date)                                      AS first_nav_date,
           max(nav_date)                                      AS last_nav_date,
           (array_agg(nav ORDER BY nav_date ASC))[1]          AS first_nav,
           (array_agg(nav ORDER BY nav_date DESC))[1]         AS last_nav,
           (array_agg(nav ORDER BY nav_date DESC))[2]         AS prev_last_nav,
           (array_agg(nav_date ORDER BY nav_date DESC))[2]    AS prev_last_date,
           md5(string_agg(nav_date::text || ':' || nav::text,
                          ',' ORDER BY nav_date))             AS fingerprint
    FROM mf.nav_history
    GROUP BY amfi_scheme_code
"""

#: Guarded family identity per scheme: the audit compares duplicate series
#: only within AMC + scheme type + scheme category + group_key, so two
#: same-named funds from different AMCs can never be paired.
_FAMILY_SQL = """
    SELECT f.amfi_scheme_code,
           f.amc_id,
           f.scheme_type,
           f.scheme_category,
           coalesce(fv.group_key, CAST(f.amfi_scheme_code AS text)) AS family_key
    FROM mf.funds f
    LEFT JOIN mf.fund_variants fv ON fv.amfi_scheme_code = f.amfi_scheme_code
"""

#: Candidate groups: same guarded family identity AND identical fingerprint.
_CANDIDATE_GROUPS_SQL = """
    SELECT fam.amc_id,
           fam.scheme_type,
           fam.scheme_category,
           fam.family_key,
           agg.fingerprint,
           array_agg(agg.amfi_scheme_code ORDER BY agg.amfi_scheme_code) AS codes
    FROM ({agg_sql}) agg
    JOIN ({family_sql}) fam ON fam.amfi_scheme_code = agg.amfi_scheme_code
    GROUP BY fam.amc_id, fam.scheme_type, fam.scheme_category,
             fam.family_key, agg.fingerprint
    HAVING count(DISTINCT agg.amfi_scheme_code) > 1
""".replace("{agg_sql}", _AGG_SQL).replace("{family_sql}", _FAMILY_SQL)


@dataclass
class SchemeSeries:
    """One scheme's aggregated history (pass 1)."""

    amfi_scheme_code: int
    observation_count: int
    distinct_nav_count: int
    first_nav_date: date
    last_nav_date: date
    first_nav: Decimal
    last_nav: Decimal
    prev_last_nav: Optional[Decimal]
    prev_last_date: Optional[date]
    fingerprint: str


@dataclass
class AssessmentRun:
    """Everything one audit pass determined, before persistence."""

    series: dict[int, SchemeSeries] = field(default_factory=dict)
    signals: dict[int, list[str]] = field(default_factory=dict)
    evidence: dict[int, dict[str, Any]] = field(default_factory=dict)
    family: dict[int, dict[str, Any]] = field(default_factory=dict)
    nav_rows: int = 0
    dataset_version: int = 0

    def signal_codes(self, signal: str) -> list[int]:
        return sorted(code for code, sigs in self.signals.items() if signal in sigs)

    def add_signal(self, code: int, signal: str) -> None:
        self.signals.setdefault(code, []).append(signal)
        self.evidence.setdefault(code, {})[signal] = {}


def _row_to_series(row: dict[str, Any]) -> SchemeSeries:
    return SchemeSeries(
        amfi_scheme_code=int(row["amfi_scheme_code"]),
        observation_count=int(row["observation_count"]),
        distinct_nav_count=int(row["distinct_nav_count"]),
        first_nav_date=row["first_nav_date"],
        last_nav_date=row["last_nav_date"],
        first_nav=row["first_nav"],
        last_nav=row["last_nav"],
        prev_last_nav=row["prev_last_nav"],
        prev_last_date=row["prev_last_date"],
        fingerprint=row["fingerprint"],
    )


def _fetch_series(conn, code: int) -> list[tuple[date, Decimal]]:
    rows = conn.execute(
        "SELECT nav_date, nav FROM mf.nav_history WHERE amfi_scheme_code = %(c)s ORDER BY nav_date",
        {"c": code},
    ).fetchall()
    return [(r["nav_date"], r["nav"]) for r in rows]


def scan_aggregates(conn) -> tuple[dict[int, SchemeSeries], int]:
    """Pass 1: one aggregate per scheme over the whole history.

    Returns (series_by_code, total_nav_rows).
    """
    rows = conn.execute(_AGG_SQL).fetchall()
    series = {int(r["amfi_scheme_code"]): _row_to_series(r) for r in rows}
    nav_rows = sum(s.observation_count for s in series.values())
    return series, nav_rows


def load_families(conn) -> dict[int, dict[str, Any]]:
    rows = conn.execute(_FAMILY_SQL).fetchall()
    return {int(r["amfi_scheme_code"]): dict(r) for r in rows}


def detect_constant_series(run: AssessmentRun) -> None:
    for code, s in sorted(run.series.items()):
        if s.observation_count >= CONSTANT_MIN_OBSERVATIONS and s.distinct_nav_count == 1:
            run.add_signal(code, SIGNAL_CONSTANT)
            run.evidence[code][SIGNAL_CONSTANT] = {
                "value": str(s.first_nav),
                "observations": s.observation_count,
                "first_nav_date": s.first_nav_date.isoformat(),
                "last_nav_date": s.last_nav_date.isoformat(),
            }


def detect_terminal_face_value_resets(run: AssessmentRun) -> None:
    for code, s in sorted(run.series.items()):
        if (
            s.last_nav == FACE_VALUE
            and s.prev_last_nav is not None
            and s.prev_last_nav >= TERMINAL_RESET_PREV_FLOOR
        ):
            run.add_signal(code, SIGNAL_TERMINAL_RESET)
            run.evidence[code][SIGNAL_TERMINAL_RESET] = {
                "last_nav": str(s.last_nav),
                "last_nav_date": s.last_nav_date.isoformat(),
                "prev_nav": str(s.prev_last_nav),
                "prev_nav_date": s.prev_last_date.isoformat() if s.prev_last_date else None,
            }


def detect_duplicate_variant_series(conn, run: AssessmentRun) -> None:
    """Duplicate series within a guarded family identity, exactly verified.

    The fingerprint narrows candidates to at most a handful of groups; each
    candidate group's full series are then fetched and compared value for
    value. Only codes verified identical to at least one other code in the
    same family earn the signal.
    """
    groups = conn.execute(_CANDIDATE_GROUPS_SQL).fetchall()
    for g in groups:
        codes = [int(c) for c in g["codes"]]
        series = {code: _fetch_series(conn, code) for code in codes}
        reference = series[codes[0]]
        # Exact verification: same length, same (date, value) sequence. The
        # fingerprint only narrows; the full comparison decides.
        verified = [code for code in codes if series[code] == reference]
        if len(verified) < 2:
            continue
        family_id = {
            "amc_id": g["amc_id"],
            "scheme_type": g["scheme_type"],
            "scheme_category": g["scheme_category"],
            "group_key": g["family_key"],
        }
        for code in verified:
            run.add_signal(code, SIGNAL_DUPLICATE)
            run.evidence[code][SIGNAL_DUPLICATE] = {
                "family": family_id,
                "counterpart_codes": sorted(c for c in verified if c != code),
                "fingerprint": g["fingerprint"],
                "observations": len(series[code]),
                "first_nav_date": series[code][0][0].isoformat(),
                "last_nav_date": series[code][-1][0].isoformat(),
            }


def build_summary(run: AssessmentRun, *, duration_ms: Optional[int] = None) -> dict[str, Any]:
    """Finite, sorted JSON summary: methodology, dataset, counts, codes."""
    summary: dict[str, Any] = {
        "status": "NAV_INTEGRITY_AUDIT_OK",
        "methodology_version": METHODOLOGY_VERSION,
        "dataset_version": run.dataset_version,
        "nav_rows_scanned": run.nav_rows,
        "schemes_assessed": len(run.series),
        "signals": {
            SIGNAL_CONSTANT: {
                "count": len(run.signal_codes(SIGNAL_CONSTANT)),
                "codes": run.signal_codes(SIGNAL_CONSTANT),
            },
            SIGNAL_DUPLICATE: {
                "count": len(run.signal_codes(SIGNAL_DUPLICATE)),
                "codes": run.signal_codes(SIGNAL_DUPLICATE),
            },
            SIGNAL_TERMINAL_RESET: {
                "count": len(run.signal_codes(SIGNAL_TERMINAL_RESET)),
                "codes": run.signal_codes(SIGNAL_TERMINAL_RESET),
            },
        },
        "affected_codes": sorted(run.signals) if run.signals else [],
    }
    if duration_ms is not None:
        summary["duration_ms"] = duration_ms
    return summary


def persist_assessments(conn, run: AssessmentRun) -> int:
    """Upsert the assessment table — the audit's ONLY write target.

    One row per assessed scheme (signals may be empty for clean schemes), so
    the API can distinguish not-assessed (no row) from assessed-and-clean
    (row, empty signals). Rows from earlier runs whose scheme no longer has
    NAV observations are cleared. Returns the row count written.
    """
    conn.execute(
        """
        DELETE FROM mf.nav_quality_assessments
        WHERE amfi_scheme_code NOT IN (SELECT amfi_scheme_code FROM mf.nav_history
                                       GROUP BY amfi_scheme_code)
        """
    )
    rows = []
    for code, s in run.series.items():
        rows.append(
            (
                code,
                METHODOLOGY_VERSION,
                run.dataset_version,
                s.observation_count,
                s.distinct_nav_count,
                s.first_nav_date,
                s.last_nav_date,
                sorted(run.signals.get(code, [])),
                json.dumps(run.evidence.get(code, {}), sort_keys=True),
            )
        )
    with conn.cursor() as cur:
        for i in range(0, len(rows), 1000):
            cur.executemany(
                """
                INSERT INTO mf.nav_quality_assessments (
                    amfi_scheme_code, methodology_version, assessed_dataset_version,
                    assessed_at, observation_count, distinct_nav_count,
                    first_nav_date, last_nav_date, signals, evidence
                ) VALUES (%s, %s, %s, now(), %s, %s, %s, %s, %s, %s)
                ON CONFLICT (amfi_scheme_code) DO UPDATE SET
                    methodology_version      = EXCLUDED.methodology_version,
                    assessed_dataset_version = EXCLUDED.assessed_dataset_version,
                    assessed_at              = EXCLUDED.assessed_at,
                    observation_count        = EXCLUDED.observation_count,
                    distinct_nav_count       = EXCLUDED.distinct_nav_count,
                    first_nav_date           = EXCLUDED.first_nav_date,
                    last_nav_date            = EXCLUDED.last_nav_date,
                    signals                  = EXCLUDED.signals,
                    evidence                 = EXCLUDED.evidence
                """,
                rows[i : i + 1000],
            )
    return len(rows)


def assess(conn, *, write: bool = True) -> dict[str, Any]:
    """Run the complete assessment on the current database.

    ``conn`` must use the dict_row factory (as PostgresStore connections do)
    and, for ``write=True``, the caller should run it inside one transaction
    so the assessment refresh is all-or-nothing.

    Read-only over mf.nav_history; the only writes go to
    mf.nav_quality_assessments (and only when ``write`` is true).
    """
    dataset = conn.execute(
        "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
    ).fetchone()
    run = AssessmentRun()
    run.dataset_version = int(dataset["dataset_version"]) if dataset else 0
    run.series, run.nav_rows = scan_aggregates(conn)
    run.family = load_families(conn)
    detect_constant_series(run)
    detect_terminal_face_value_resets(run)
    detect_duplicate_variant_series(conn, run)
    written = persist_assessments(conn, run) if write else 0
    summary = build_summary(run)
    summary["assessments_written"] = written
    if not write:
        summary["status"] = "NAV_INTEGRITY_AUDIT_DRY_RUN"
    return summary
