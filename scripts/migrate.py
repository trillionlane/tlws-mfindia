#!/usr/bin/env python3
"""Apply pending forward migrations with checksum verification."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402
from mfdataindia.audit import nav_integrity  # noqa: E402
from mfdataindia.jobs.nav_calculations import recompute  # noqa: E402


def main() -> int:
    try:
        dsn = resolve_dsn(purpose="the migration runner")
    except DsnError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.info("target database: %s", describe_dsn(dsn))
    with PostgresStore(dsn, use_copy=True) as store:
        applied = store.apply_migrations(ROOT / "sql")
        # Seed the new projections BEFORE deployment promotes a strict API.
        # Retry also repairs an interrupted baseline after 022 itself committed.
        # Published/source facts and authoritative NAVs are never rewritten.
        with store.transaction() as conn:
            from mfdataindia.store.locks import acquire_nav_data_operation_lock

            acquire_nav_data_operation_lock(conn)
            conn.execute("SET LOCAL statement_timeout = '120s'")
            codes = [r['amfi_scheme_code'] for r in conn.execute(
                "SELECT r.amfi_scheme_code FROM mf.nav_source_revisions r "
                "LEFT JOIN mf.nav_calculations c USING(amfi_scheme_code) "
                "WHERE (c.amfi_scheme_code IS NULL OR c.source_revision<>r.revision OR c.methodology_version<>'1') "
                "AND EXISTS(SELECT 1 FROM mf.nav_history n WHERE n.amfi_scheme_code=r.amfi_scheme_code) "
                "ORDER BY r.amfi_scheme_code"
            ).fetchall()]
            if codes:
                recompute(conn, codes)
            missing_quality = conn.execute(
                "SELECT 1 FROM mf.nav_source_revisions r LEFT JOIN mf.nav_quality_assessments q USING(amfi_scheme_code) "
                "CROSS JOIN mf.dataset_summary ds WHERE ds.singleton AND EXISTS "
                "(SELECT 1 FROM mf.nav_history n WHERE n.amfi_scheme_code=r.amfi_scheme_code) AND "
                "(q.assessed_source_revision IS DISTINCT FROM r.revision OR q.assessed_dataset_version IS DISTINCT FROM ds.dataset_version "
                "OR q.methodology_version IS DISTINCT FROM '1') LIMIT 1"
            ).fetchone()
            if missing_quality:
                nav_integrity.assess(conn, verify_current=True)
        logging.info("versioned calculation baseline ready; recomputed %d funds", len(codes))
    if applied:
        logging.info("applied %d migration(s): %s", len(applied), ", ".join(applied))
    else:
        logging.info("schema already current; no migrations applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
