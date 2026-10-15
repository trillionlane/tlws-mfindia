"""Hash-bound private DEV batches; no automatic provider fetch or gate vars."""

from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from pydantic import Field, model_validator
from mfdataindia.enrichment import idcw_catalog as engine, idcw_operations, idcw_store
from mfdataindia.enrichment.distribution_deployment import DistributionSource
from mfdataindia.enrichment.portfolio_deployment import download
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.enrichment.schemas import Model
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import seal_evidence


class IdcwBatch(Model):
    requests: list[engine.IdcwRequest] = Field(min_length=1, max_length=4)
    sources: dict[str, DistributionSource]

    @model_validator(mode="after")
    def exact_sources(self):
        codes = [r.identity.code for r in self.requests]
        if codes != sorted(set(codes)):
            raise ValueError("unique sorted fund requests required")
        required = {}
        for request in self.requests:
            for role in ("history", "registry"):
                source = getattr(request, role)
                key = source.content_hash
                if key in required and required[key] != source:
                    raise ValueError("shared original has inconsistent provenance")
                required[key] = source
        if set(self.sources) != set(required):
            raise ValueError("exact original source set required")
        if any(self.sources[key].intake != intake for key, intake in required.items()):
            raise ValueError("original intake differs from request")
        return self

    def bind(self, manifest):
        if set(manifest.cohort) != {r.identity.code for r in self.requests}:
            raise ValueError("exact approved IDCW cohort required")
        for key, source in self.sources.items():
            if (
                source.object_name
                != f"official-idcw-catalog-rollouts/{manifest.rollout_id}/{key}.pdf"
            ):
                raise ValueError("source object outside approved rollout")
        return fingerprint(self.model_dump(mode="json"))


def selection(manifest, environment):
    batch = IdcwBatch.model_validate_json(environment["IDCW_BATCH"])
    if batch.bind(manifest) != environment["IDCW_BATCH_HASH"]:
        raise ValueError("IDCW approval binding differs")
    return batch


def execute(manifest, environment, operation, session, *, root=Path("/app")):
    if operation not in ("install", "append"):
        raise ValueError("unsupported IDCW operation")
    batch = selection(manifest, environment)
    dsn = pinned_dsn(
        manifest,
        environment,
        "mfdata_admin" if operation == "install" else idcw_store.evidence.ROLES[0],
    )
    raw = (
        {key: download(source, session) for key, source in batch.sources.items()}
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
        conn.execute("SET LOCAL transaction_timeout='420s'")
        if operation == "install":
            result = idcw_operations.install(conn, root)
        else:
            documents = idcw_store.append_batch(
                conn,
                batch.requests,
                [
                    {
                        role: raw[getattr(request, role).content_hash]
                        for role in ("history", "registry")
                    }
                    for request in batch.requests
                ],
            )
            result = {
                "snapshots": [
                    {
                        "code": d["code"],
                        "snapshot_id": d["snapshot_id"],
                        "observed_event_count": len(d["events"]),
                    }
                    for d in documents
                ],
                "source_rows_changed": 0,
                "coverage_state": "unknown",
                "analytics_eligible": False,
            }
    return seal_evidence(
        manifest,
        "idcw-" + operation,
        {"level": "DEV_DATABASE", "selection_hash": batch.bind(manifest), **result},
    )
