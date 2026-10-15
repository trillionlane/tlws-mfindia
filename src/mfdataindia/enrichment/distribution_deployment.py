"""Exact approved DEV PDF bundle; no browser fetching, source edits or gate vars."""

from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from pydantic import Field, StrictInt, model_validator
from mfdataindia.enrichment import distribution_operations, distribution_store
from mfdataindia.enrichment.idcw_discovery import (
    IdcwDiscoveryRequest,
    IdcwDiscoverySource,
    MAX_BYTES,
)
from mfdataindia.enrichment.schemas import Model
from mfdataindia.enrichment.portfolio_deployment import download
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import seal_evidence


class DistributionSource(Model):
    intake: IdcwDiscoverySource
    object_name: str
    generation: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    byte_length: StrictInt = Field(gt=0, le=MAX_BYTES)


class DistributionBatch(Model):
    request: IdcwDiscoveryRequest
    sources: dict[str, DistributionSource]

    @model_validator(mode="after")
    def exact_sources(self):
        if set(self.sources) != {"history", "notice", "registry"}:
            raise ValueError("exact three-source distribution bundle required")
        for role, source in self.sources.items():
            if source.intake != getattr(self.request, role):
                raise ValueError("source intake and request differ")
        return self

    def bind(self, manifest):
        if self.request.identity.code not in manifest.cohort:
            raise ValueError("distribution code outside approved cohort")
        for role, source in self.sources.items():
            if (
                source.object_name
                != "official-distribution-rollouts/" + manifest.rollout_id + "/" + role + ".pdf"
            ):
                raise ValueError("source object outside exact distribution rollout")
        return fingerprint(self.model_dump(mode="json"))


def selection(manifest, environment):
    batch = DistributionBatch.model_validate_json(environment["DISTRIBUTION_BATCH"])
    if batch.bind(manifest) != environment["DISTRIBUTION_BATCH_HASH"]:
        raise ValueError("distribution approval binding differs")
    return batch


def execute(manifest, environment, operation, session, *, root=Path("/app")):
    if operation not in ("install", "append"):
        raise ValueError("unsupported distribution operation")
    batch = selection(manifest, environment)
    dsn = pinned_dsn(
        manifest,
        environment,
        "mfdata_admin" if operation == "install" else distribution_store.evidence.ROLES[0],
    )
    sources = (
        {role: download(source, session) for role, source in batch.sources.items()}
        if operation == "append"
        else None
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
            result = distribution_operations.install(conn, root)
        else:
            document = distribution_store.append(conn, batch.request, sources)
            result = {
                "snapshot_id": document["snapshot_id"],
                "observed_event_count": len(document["events"]),
                "source_rows_changed": 0,
                "coverage_state": "unknown",
                "analytics_eligible": False,
            }
    return seal_evidence(
        manifest,
        "distribution-" + operation,
        {"level": "DEV_DATABASE", "selection_hash": batch.bind(manifest), **result},
    )
