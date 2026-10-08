"""Resumable NAV-history backfill from AMFI's bulk report.

The required delivery is a **last-5-years** NAV window for Regular Plan schemes.
This job fetches AMFI's ``DownloadNAVHistoryReport_Po.aspx`` in date chunks and
scheme-type slices, one checkpoint per (chunk, tp) in ``mf.ingest_checkpoints``,
so a multi-hour run can be interrupted and resumed without re-fetching.

Resumability contract
---------------------
* Before work: ``checkpoint_should_run`` — DONE chunks are skipped.
* On success: ``checkpoint_done`` with the row count.
* On failure: ``checkpoint_failed`` with the error; a later run retries it.
* A failed chunk in non-strict mode is recorded and skipped so the rest of the
  run continues; in strict mode the exception propagates.

Scope
-----
Only in-scope codes (Regular Plan, per ``mf.funds.in_scope``) are written when
``in_scope_only=True`` (the default) — history rows for Direct/ETF/blank-plan
schemes are parsed but not stored, which halves the volume. ``mf.funds`` must be
populated first (via the daily NAVAll.txt load), because ``mf.nav_history`` has a
foreign key to it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from mfdataindia.ingest.amfi_client import AmfiClient
from mfdataindia.ingest.amfi_nav_history import parse_nav_history_report
from mfdataindia.load.amfi_to_store import nav_rows
from mfdataindia.store.postgres import PostgresStore

log = logging.getLogger(__name__)

SOURCE = "AMFI_HISTORY"
KIND = "NAV_HISTORY"


@dataclass(slots=True)
class BackfillReport:
    """Rollup of one backfill run."""

    chunks_done: int = 0
    chunks_skipped: int = 0   # already DONE in a prior run
    chunks_failed: int = 0
    nav_inserted: int = 0
    nav_updated: int = 0
    nav_unchanged: int = 0
    failures: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunks_done": self.chunks_done,
            "chunks_skipped": self.chunks_skipped,
            "chunks_failed": self.chunks_failed,
            "nav_inserted": self.nav_inserted,
            "nav_updated": self.nav_updated,
            "nav_unchanged": self.nav_unchanged,
            "failures": self.failures,
            "elapsed_s": round(self.elapsed_s, 1),
        }


def _checkpoint_key(chunk_from: date, chunk_to: date, tp: int) -> str:
    return f"tp{tp}:{chunk_from.isoformat()}:{chunk_to.isoformat()}"


def backfill_nav_history(
    store: PostgresStore,
    client: AmfiClient,
    from_date: date,
    to_date: date,
    *,
    chunk_days: int = 90,
    in_scope_only: bool = True,
    strict: bool = True,
    max_chunks: Optional[int] = None,
) -> BackfillReport:
    """Backfill NAV history for [from_date, to_date], resuming from checkpoints.

    Requires ``mf.funds`` to be loaded first (the daily NAVAll.txt load).
    """
    scope_codes = store.in_scope_codes() if in_scope_only else None
    if in_scope_only and not scope_codes:
        raise RuntimeError(
            "mf.funds has no in-scope schemes; load NAVAll.txt first")

    report = BackfillReport()
    chunk_count = 0
    for chunk_from, chunk_to, tp in client.iter_nav_history_ranges(
            from_date, to_date, chunk_days=chunk_days):
        key = _checkpoint_key(chunk_from, chunk_to, tp)
        if not store.checkpoint_should_run(SOURCE, KIND, key):
            report.chunks_skipped += 1
            log.info("skip (already DONE): %s", key)
            continue

        if max_chunks is not None and chunk_count >= max_chunks:
            break
        chunk_count += 1

        store.checkpoint_start(SOURCE, KIND, key)
        try:
            fetched = client.fetch_nav_history(chunk_from, chunk_to, tp)
            rows, _ = parse_nav_history_report(fetched.text)
            if scope_codes is not None:
                rows = [
                    r for r in rows
                    if r.amfi_scheme_code.isdigit()
                    and int(r.amfi_scheme_code) in scope_codes
                ]
            result = store.upsert_nav(
                nav_rows(rows, source=SOURCE))

            store.record_fetch(
                fetched.as_source_metadata(SOURCE, KIND, key)
                | {"records_in": len(rows),
                   "records_ok": result.inserted + result.updated,
                   "duration_ms": fetched.duration_ms})
            done = result.inserted + result.updated
            store.checkpoint_done(SOURCE, KIND, key, done, cursor_value=key)
            report.chunks_done += 1
            report.nav_inserted += result.inserted
            report.nav_updated += result.updated
            report.nav_unchanged += result.unchanged
            log.info("chunk %s: %d rows -> %d new/%d updated",
                     key, len(rows), result.inserted, result.updated)
        except Exception as exc:  # noqa: BLE001 - record, maybe continue
            report.chunks_failed += 1
            report.failures.append(f"{key}: {exc}")
            store.checkpoint_failed(SOURCE, KIND, key, str(exc)[:500])
            log.exception("chunk %s failed", key)
            if strict:
                raise

    log.info("backfill complete: %s", report.as_dict())
    if report.nav_inserted or report.nav_updated:
        store.refresh_dataset_summary("nav_history_backfill")
    return report
