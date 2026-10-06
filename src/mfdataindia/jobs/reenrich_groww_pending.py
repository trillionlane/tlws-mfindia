"""Re-enrichment pass for the three Groww fields nothing has fetched yet.

Targets ``registrar_agent``, ``base_expense_ratio`` and ``expense_ratio_history``
-- the fields Groww serves but which are 100% NULL because they were mapped and
then silently dropped by the column projection until that bug was fixed.

Why this is a separate job rather than ``enrich_groww --all``:

* **Checkpoints would skip everything.** ``enrich_groww`` resumes off
  ``mf.ingest_checkpoints``, and 4,271 funds are already ``DONE`` for
  ``ENRICH_FUND``. A re-run would skip exactly the funds that need revisiting.
  This job uses its own ``ENRICH_PENDING`` entity kind, so the original
  checkpoint history is preserved and this pass is independently resumable.
* **Targets come from the status view.** The work list is derived from
  ``mf.v_fund_data_status``, so it is precisely the funds that still have a
  pending gap -- live and in scope. Closed funds are excluded; they are not
  expected to receive re-enrichment (``pending_expected`` is 0 for them).

Writes go through ``load_groww_fund``, which is non-destructive by construction:
``fill_only`` on ``fund_facts``, ``overwrite_columns`` limited to Groww-owned
columns, ``only_if_empty`` on holdings. Scripbox values and existing holdings
snapshots cannot be replaced by this job.

Note that ``super_category`` / ``sub_category`` are deliberately *not* part of
this pass -- Groww's ``super_category`` is the fund name, not a category, and its
real hierarchy is already stored as ``asset_class`` / ``sub_asset_class``.
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
#: Distinct from ENRICH_FUND so the existing DONE history does not skip this pass.
KIND_PENDING = "ENRICH_PENDING"

#: The columns this pass is meant to close, for before/after verification.
PENDING_COLUMNS = ("registrar_agent", "base_expense_ratio", "expense_ratio_history")


@dataclass(slots=True)
class ReenrichReport:
    targeted: int = 0
    enriched: int = 0
    no_match: int = 0   # slug candidates never resolved to the right ISIN
    failed: int = 0
    skipped: int = 0    # already DONE in a previous run of this pass
    dry_run: bool = False
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in
                ("targeted", "enriched", "no_match", "failed", "skipped", "dry_run")} \
               | {"failures": self.failures[:20]}


def pending_targets(store: PostgresStore) -> list[dict[str, Any]]:
    """Live in-scope funds that still have at least one pending Groww gap.

    Driven by ``mf.v_fund_data_status`` so the definition of "still missing" lives
    in exactly one place -- the same view the completeness report uses.
    """
    with store.connect().cursor() as cur:
        return cur.execute(
            """
            SELECT f.amfi_scheme_code, f.scheme_name, f.plan_type, f.option_type,
                   f.isin_primary, a.amfi_amc_name, v.pending_items
            FROM mf.v_fund_data_status v
            JOIN mf.funds f ON f.amfi_scheme_code = v.amfi_scheme_code
            JOIN mf.amcs  a ON a.amc_id = f.amc_id
            WHERE v.in_scope
              AND NOT v.is_closed
              AND cardinality(v.pending_items) > 0
              AND f.isin_primary IS NOT NULL
            ORDER BY f.amfi_scheme_code
            """
        ).fetchall()


def reenrich_pending(
    store: PostgresStore,
    client: GrowwClient,
    *,
    max_funds: Optional[int] = None,
    dry_run: bool = False,
    strict: bool = False,
) -> ReenrichReport:
    """Fill the pending Groww fields for every live in-scope fund that lacks them.

    ``dry_run`` resolves targets and reports the work list without fetching or
    writing anything -- use it to confirm the target count and the DSN first.
    """
    report = ReenrichReport(dry_run=dry_run)
    targets = pending_targets(store)
    report.targeted = len(targets)
    log.info("groww pending re-enrichment: %d targets%s",
             len(targets), " (dry run)" if dry_run else "")
    if dry_run:
        return report

    done = 0
    for f in targets:
        if max_funds is not None and done >= max_funds:
            break
        code = int(f["amfi_scheme_code"])
        key = str(code)
        if not store.checkpoint_should_run(SOURCE, KIND_PENDING, key):
            report.skipped += 1
            continue
        store.checkpoint_start(SOURCE, KIND_PENDING, key)
        try:
            slug, data = client.fetch_by_isin(
                f["scheme_name"], f["plan_type"], f["option_type"], f["isin_primary"])
            if data is None:
                report.no_match += 1
                store.checkpoint_done(SOURCE, KIND_PENDING, key, 0,
                                      cursor_value="no_match")
            else:
                load_groww_fund(store, data, code, f.get("amfi_amc_name"))
                report.enriched += 1
                store.checkpoint_done(SOURCE, KIND_PENDING, key, 1, cursor_value=slug)
            done += 1
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            report.failures.append(f"{code}: {exc}")
            store.checkpoint_failed(SOURCE, KIND_PENDING, key, str(exc)[:500])
            log.exception("groww pending re-enrich failed for %s", code)
            if strict:
                raise
    log.info("groww pending re-enrichment: %s", report.as_dict())
    return report
