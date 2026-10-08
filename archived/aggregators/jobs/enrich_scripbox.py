"""Resumable Scripbox enrichment crawl.

Scripbox is the reliable enrichment source (real regular-plan slugs + ISINs).
The crawl is fully resumable from ``mf.ingest_checkpoints``, which doubles as the
work queue:

  Phase 1 (discovery, checkpoint per AMC):
      /mutual-fund/amc                       -> AMC slugs
      /mutual-fund/amc/{slug}/isin-fair-market-values -> funds + fund_variant[]
      Each regular variant (isin_code) is registered as a PENDING ENRICH_FUND
      checkpoint, but only if its ISIN maps to a fund in mf.funds (and, by
      default, an in-scope one). This focuses the crawl on what we deliver.

  Phase 2 (fetch, checkpoint per fund):
      Drain PENDING ENRICH_FUND checkpoints; fetch each fund page, load the
      factsheet into fund_facts + fund_opinions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from mfdataindia.ingest.scripbox_client import ScripboxClient, regular_variants
from mfdataindia.load.scripbox_to_store import load_factsheets
from mfdataindia.store.postgres import PostgresStore

log = logging.getLogger(__name__)

SOURCE = "SCRIPBOX"
KIND_AMC = "ENRICH_AMC"
KIND_FUND = "ENRICH_FUND"


@dataclass(slots=True)
class EnrichReport:
    amcs_done: int = 0
    amcs_skipped: int = 0
    funds_discovered: int = 0
    funds_enriched: int = 0
    funds_skipped: int = 0
    funds_failed: int = 0
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in
                ("amcs_done", "amcs_skipped", "funds_discovered",
                 "funds_enriched", "funds_skipped", "funds_failed")} | {"failures": self.failures}


def _isin_to_code(store: PostgresStore) -> dict[str, int]:
    """Map each fund's ISINs -> amfi_scheme_code (both AMFI columns)."""
    out: dict[str, int] = {}
    with store.connect().cursor() as cur:
        for r in cur.execute(
                "SELECT amfi_scheme_code, isin_growth_or_div_payout, isin_div_reinvest "
                "FROM mf.funds").fetchall():
            for isin in (r["isin_growth_or_div_payout"], r["isin_div_reinvest"]):
                if isin:
                    out[isin] = int(r["amfi_scheme_code"])
    return out


def _discover(store: PostgresStore, client: ScripboxClient, inscope: set[int],
              report: EnrichReport) -> None:
    """Register a PENDING ENRICH_FUND checkpoint per regular variant in scope."""
    isin_map = _isin_to_code(store)
    amcs = client.fetch_amc_list()
    log.info("scripbox discovery: %d AMCs", len(amcs))
    for amc in amcs:
        slug = amc.get("amc_slug")
        if not slug:
            continue
        if not store.checkpoint_should_run(SOURCE, KIND_AMC, slug):
            report.amcs_skipped += 1
            continue
        store.checkpoint_start(SOURCE, KIND_AMC, slug)
        try:
            funds = client.fetch_amc_funds(slug)
            for v in regular_variants(funds):
                isin = v.get("isin_code")
                code = isin_map.get(isin) if isin else None
                if code is None or code not in inscope:
                    continue
                store.checkpoint_register(
                    SOURCE, KIND_FUND, v["fund_slug"],
                    cursor_value=json.dumps({"isin": isin, "code": code}))
                report.funds_discovered += 1
            store.checkpoint_done(SOURCE, KIND_AMC, slug, report.funds_discovered)
            report.amcs_done += 1
        except Exception as exc:  # noqa: BLE001
            report.failures.append(f"amc:{slug}: {exc}")
            store.checkpoint_failed(SOURCE, KIND_AMC, slug, str(exc)[:500])
            log.exception("AMC discovery failed: %s", slug)


def _fetch_pending(store: PostgresStore, client: ScripboxClient, report: EnrichReport,
                   max_funds: Optional[int], strict: bool) -> None:
    """Drain PENDING ENRICH_FUND checkpoints: fetch page, load factsheet."""
    done = 0
    while True:
        pending = [r for r in store.pending_checkpoints(SOURCE, KIND_FUND)
                   if r["status"] in ("PENDING", "FAILED")]
        if not pending:
            break
        for row in pending:
            if max_funds is not None and done >= max_funds:
                return
            slug = row["entity_key"]
            store.checkpoint_start(SOURCE, KIND_FUND, slug)
            try:
                fs = client.fetch_fund(slug)
                if fs is None:
                    report.funds_skipped += 1
                    store.checkpoint_done(SOURCE, KIND_FUND, slug, 0)
                    done += 1
                    continue
                res = load_factsheets(store, [fs])
                ok = res["fund_facts"]["inserted"] + res["fund_facts"]["updated"]
                report.funds_enriched += 1 if ok else 0
                report.funds_skipped += 0 if ok else 1
                store.checkpoint_done(SOURCE, KIND_FUND, slug, ok)
                done += 1
            except Exception as exc:  # noqa: BLE001
                report.funds_failed += 1
                report.failures.append(f"fund:{slug}: {exc}")
                store.checkpoint_failed(SOURCE, KIND_FUND, slug, str(exc)[:500])
                log.exception("fund fetch failed: %s", slug)
                if strict:
                    raise


def enrich_scripbox(
    store: PostgresStore,
    client: ScripboxClient,
    *,
    in_scope_only: bool = True,
    max_funds: Optional[int] = None,
    strict: bool = False,
) -> EnrichReport:
    """Run the full enrichment crawl (discovery + fetch), resuming from checkpoints."""
    report = EnrichReport()
    inscope = store.in_scope_codes() if in_scope_only else store.fund_codes()
    if not inscope:
        raise RuntimeError("mf.funds is empty; load NAVAll.txt first")
    _discover(store, client, inscope, report)
    _fetch_pending(store, client, report, max_funds, strict)
    log.info("scripbox enrichment: %s", report.as_dict())
    return report
