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
    if applied:
        logging.info("applied %d migration(s): %s", len(applied), ", ".join(applied))
    else:
        logging.info("schema already current; no migrations applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
