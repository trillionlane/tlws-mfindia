"""Resumable Groww enrichment backfill.

Targets in-scope funds that Scripbox did not cover (no fund_facts row) or that
lack a benchmark (multi-asset funds where Scripbox's index field is empty), plus
— optionally — everything, to pick up Groww-only fields (benchmark, fund-manager
bios, expense-ratio history, sector-tagged holdings).

Location is by derived slug, validated by the page's ISIN. Resumable via
``mf.ingest_checkpoints`` (one checkpoint per fund).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from mfdataindia.ingest.groww_client import GrowwClient
from mfdataindia.load.groww_to_store import load_groww_fund
from mfdataindia.store.postgres import PostgresStore

log = logging.getLogger(__name__)

SOURCE = "GROWW"
KIND_FUND = "ENRICH_FUND"


@dataclass(slots=True)
class GrowwReport:
    targeted: int = 0
    enriched: int = 0
    no_match: int = 0     # slug candidates didn't resolve to the right ISIN
    no_data: int = 0      # page existed but had no mfServerSideData
    failed: int = 0
    skipped: int = 0      # already DONE
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in
                ("targeted", "enriched", "no_match", "no_data", "failed", "skipped")} \
               | {"failures": self.failures}


def _targets(store: PostgresStore, mode: str) -> list[dict[str, Any]]:
    """In-scope funds needing Groww enrichment.

    mode='gaps': no facts, or facts present but no benchmark.
    mode='all':  every live in-scope fund.
    """
    with store.connect().cursor() as cur:
        if mode == "all":
            return cur.execute(
                "SELECT f.amfi_scheme_code, f.scheme_name, f.plan_type, "
                "f.option_type, f.isin_primary, a.amfi_amc_name "
                "FROM mf.funds f "
                "JOIN mf.amcs a ON a.amc_id = f.amc_id "
                "WHERE f.in_scope AND NOT f.is_defunct AND f.isin_primary IS NOT NULL "
                "ORDER BY f.amfi_scheme_code").fetchall()
        return cur.execute(
            """
            SELECT f.amfi_scheme_code, f.scheme_name, f.plan_type, f.option_type,
                   f.isin_primary, a.amfi_amc_name
            FROM mf.funds f
            JOIN mf.amcs a ON a.amc_id = f.amc_id
            LEFT JOIN mf.fund_facts ff ON ff.amfi_scheme_code = f.amfi_scheme_code
            WHERE f.in_scope AND NOT f.is_defunct AND f.isin_primary IS NOT NULL
              AND (ff.amfi_scheme_code IS NULL OR ff.benchmark IS NULL)
            ORDER BY f.amfi_scheme_code
            """).fetchall()


def enrich_groww(
    store: PostgresStore,
    client: GrowwClient,
    *,
    mode: str = "gaps",
    max_funds: Optional[int] = None,
    strict: bool = False,
) -> GrowwReport:
    """Backfill fund facts from Groww. mode='gaps' (default) or 'all'."""
    report = GrowwReport()
    targets = _targets(store, mode)
    report.targeted = len(targets)
    log.info("groww enrichment (%s): %d targets", mode, len(targets))

    done = 0
    for f in targets:
        if max_funds is not None and done >= max_funds:
            break
        code = int(f["amfi_scheme_code"])
        key = str(code)
        if not store.checkpoint_should_run(SOURCE, KIND_FUND, key):
            report.skipped += 1
            continue
        store.checkpoint_start(SOURCE, KIND_FUND, key)
        try:
            slug, data = client.fetch_by_isin(
                f["scheme_name"], f["plan_type"], f["option_type"], f["isin_primary"])
            if data is None:
                report.no_match += 1
                store.checkpoint_done(SOURCE, KIND_FUND, key, 0, cursor_value="no_match")
            else:
                load_groww_fund(store, data, code, f.get("amfi_amc_name"))
                report.enriched += 1
                store.checkpoint_done(SOURCE, KIND_FUND, key, 1, cursor_value=slug)
            done += 1
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            report.failures.append(f"{code}: {exc}")
            store.checkpoint_failed(SOURCE, KIND_FUND, key, str(exc)[:500])
            log.exception("groww enrich failed for %s", code)
            if strict:
                raise
    log.info("groww enrichment: %s", report.as_dict())
    return report
