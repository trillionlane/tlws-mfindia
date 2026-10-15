"""Fixed DEV original-source import and bounded portfolio append workers."""

import hashlib
import time
from pathlib import Path
from urllib.parse import quote
import psycopg
from psycopg.rows import dict_row
from pydantic import Field, StrictInt
from mfdataindia.enrichment import (
    portfolio_schemas as schemas,
    portfolio_store,
    portfolio_operations,
)
from mfdataindia.enrichment.deployment import BUCKET, pinned_dsn
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import seal_evidence


class Source(schemas.Model):
    intake: schemas.Intake
    object_name: str
    generation: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    byte_length: StrictInt = Field(gt=0, le=8388608)

    def bind(self, manifest):
        suffix = "archive.zip" if self.intake.media_type == "application/zip" else "identity.pdf"
        if self.object_name != "official-portfolio-rollouts/" + manifest.rollout_id + "/" + suffix:
            raise ValueError("portfolio source object outside exact rollout")
        return fingerprint(self.model_dump(mode="json"))


def download(source, session):
    session.trust_env = False
    response = session.get(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
        timeout=10,
        allow_redirects=False,
    )
    if response.status_code != 200 or not isinstance(response.json().get("access_token"), str):
        raise ValueError("workload storage credentials unavailable")
    token = response.json()["access_token"]
    url = (
        "https://storage.googleapis.com/storage/v1/b/"
        + BUCKET
        + "/o/"
        + quote(source.object_name, safe="")
    )
    started = time.monotonic()
    with session.get(
        url,
        params={"alt": "media", "generation": source.generation},
        headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity"},
        stream=True,
        timeout=(10, 30),
        allow_redirects=False,
    ) as result:
        if (
            result.status_code != 200
            or result.headers.get("Content-Encoding", "identity") != "identity"
            or result.headers.get("x-goog-generation") != source.generation
            or result.headers.get("Content-Length") != str(source.byte_length)
        ):
            raise ValueError("immutable portfolio transport differs")
        content = bytearray()
        for chunk in result.iter_content(65536):
            content.extend(chunk)
            if len(content) > source.byte_length or time.monotonic() - started > 60:
                raise ValueError("portfolio source transport bound exceeded")
    raw = bytes(content)
    if (
        len(raw) != source.byte_length
        or hashlib.sha256(raw).hexdigest() != source.intake.content_hash
    ):
        raise ValueError("portfolio original size/hash differs")
    return raw


def selection(manifest, environment):
    value = schemas.Selection.model_validate_json(environment["PORTFOLIO_SELECTION"])
    if fingerprint(value.model_dump(mode="json")) != environment[
        "PORTFOLIO_SELECTION_HASH"
    ] or not {t.code for t in value.targets} <= set(manifest.cohort):
        raise ValueError("portfolio selection approval differs")
    return value


def execute(manifest, environment, operation, session=None, *, root=Path("/app")):
    if operation not in ("install", "import", "publish"):
        raise ValueError("unsupported portfolio operation")
    dsn = pinned_dsn(
        manifest,
        environment,
        "mfdata_admin" if operation == "install" else portfolio_store.evidence.ROLES[0],
    )
    source = None
    if operation == "import":
        source = Source.model_validate_json(environment["PORTFOLIO_SOURCE"])
        if source.bind(manifest) != environment["PORTFOLIO_SOURCE_HASH"]:
            raise ValueError("portfolio source approval differs")
        raw = download(source, session)
    approved = selection(manifest, environment) if operation == "publish" else None
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute(
            "SET TRANSACTION ISOLATION LEVEL "
            + ("REPEATABLE READ" if operation == "install" else "READ COMMITTED")
        )
        conn.execute("SET LOCAL statement_timeout='120s'")
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL transaction_timeout='180s'")
        if operation == "install":
            result = portfolio_operations.install(conn, root)
        elif operation == "import":
            result = {
                "source_id": portfolio_store.append_source(conn, source.intake, raw),
                "source_binding_hash": source.bind(manifest),
                "accepted_portfolios": 0,
            }
        else:
            snapshots = portfolio_store.publish(conn, approved)
            result = {
                "selection_hash": fingerprint(approved.model_dump(mode="json")),
                "snapshots": [
                    {
                        "code": s["code"],
                        "snapshot_id": s["snapshot_id"],
                        "holdings_count": s["holdings_count"],
                    }
                    for s in snapshots
                ],
            }
    return seal_evidence(
        manifest,
        "portfolio-" + operation,
        {"level": "DEV_DATABASE", "source_rows_changed": 0, **result},
    )
