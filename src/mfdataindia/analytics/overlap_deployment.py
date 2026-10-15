"""One approved pinned pair, dedicated generator; no source acquisition or edits."""

from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from mfdataindia.analytics import overlap_operations, overlap_store, overlap_schemas
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import seal_evidence


def selection(manifest, environment):
    pair = overlap_schemas.Pair.model_validate_json(environment["OVERLAP_PAIR"])
    if fingerprint(pair.model_dump(mode="json")) != environment["OVERLAP_PAIR_HASH"] or not {
        pair.left_code,
        pair.right_code,
    } <= set(manifest.cohort):
        raise ValueError("overlap selection approval differs")
    return pair


def execute(manifest, environment, operation, *, root=Path("/app")):
    if operation not in ("install", "publish"):
        raise ValueError("unsupported overlap operation")
    pair = selection(manifest, environment)
    dsn = pinned_dsn(
        manifest,
        environment,
        "mfdata_admin" if operation == "install" else overlap_store.portfolios.evidence.ROLES[0],
    )
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute(
            "SET TRANSACTION ISOLATION LEVEL "
            + ("REPEATABLE READ" if operation == "install" else "READ COMMITTED")
        )
        conn.execute("SET LOCAL statement_timeout='120s'")
        conn.execute("SET LOCAL lock_timeout='5s'")
        seconds = 420 if operation == "install" else 180
        conn.execute(f"SET LOCAL transaction_timeout='{seconds}s'")
        if operation == "install":
            result = overlap_operations.install(conn, root)
        else:
            document = overlap_store.publish(conn, pair)
            result = {
                "result_id": document["result_id"],
                "observed_overlap_percent": document["observed_overlap_percent"],
                "shared_security_count": document["shared_security_count"],
                "full_economic_state": "unavailable",
            }
    return seal_evidence(
        manifest,
        "overlap-" + operation,
        {
            "level": "DEV_DATABASE",
            "source_rows_changed": 0,
            "selection_hash": fingerprint(pair.model_dump(mode="json")),
            **result,
        },
    )
