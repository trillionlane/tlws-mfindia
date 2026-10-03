"""Storage backends for MFDataIndia.

PostgreSQL is the system of record: it serves the FastAPI layer, handles daily
incremental upserts, and gives ACID guarantees across funds / nav_history /
quality_flags. Parquet is an export format derived from it, not a second store.
"""

from mfdataindia.store.postgres import (
    DEFAULT_MIGRATIONS,
    FUND_COLUMNS,
    LoadResult,
    PostgresStore,
    sha256_text,
)

__all__ = [
    "DEFAULT_MIGRATIONS",
    "FUND_COLUMNS",
    "LoadResult",
    "PostgresStore",
    "sha256_text",
]
