"""Optional verification schema/inspection; deliberately no live review writer CLI."""

from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from mfdataindia.enrichment import fact_review_store
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.preservation import capture_preservation
from mfdataindia.review.rollout import seal_evidence


def execute(manifest, environment, operation, *, root=Path("/app")):
    if operation not in ("install", "inspect"):
        raise ValueError("no live synthetic human-review operation exists")
    with psycopg.connect(
        pinned_dsn(manifest, environment, "mfdata_admin"), row_factory=dict_row
    ) as conn:
        conn.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
            + (", READ ONLY" if operation == "inspect" else "")
        )
        conn.execute("SET LOCAL statement_timeout='300s'")
        conn.execute("SET LOCAL transaction_timeout='420s'")
        conn.execute("SET LOCAL lock_timeout='5s'")
        result = (
            fact_review_store.install(conn, root)
            if operation == "install"
            else {
                "read_only": True,
                "source_rows_changed": 0,
                "preservation": capture_preservation(conn, current_facts_baseline=True),
                "verification": fact_review_store.capture(conn),
            }
        )
    return seal_evidence(
        manifest, "fact-verification-" + operation, {"level": "DEV_DATABASE", **result}
    )
