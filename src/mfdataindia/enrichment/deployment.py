"""Pinned DEV evidence operations; original sources and published facts stay untouched."""

import hashlib
import time
from pathlib import Path
from urllib.parse import quote
from contextlib import contextmanager

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from pydantic import Field, StrictInt, model_validator

from mfdataindia.enrichment import evidence, operations, schemas, store
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.rollout import CONNECTION, GEN_SA, PROJECT, seal_evidence
from mfdataindia.store.locks import acquire_nav_data_operation_lock

BUCKET = "tlws_mf_data_source"


@contextmanager
def temporary_source_read(cloud, manifest, source):
    """Arm exact-object cleanup before possibly-applied IAM writes; never delete data."""
    source.bind(manifest)
    resource = "projects/_/buckets/" + BUCKET + "/objects/" + source.object_name
    expression = "resource.name=='" + resource + "'"
    condition = "expression=" + expression + ",title=mf-evidence-source-" + manifest.rollout_id
    args = ("--project=" + PROJECT, "--member=serviceAccount:" + GEN_SA,
            "--role=roles/storage.objectViewer", "--condition=" + condition, "--quiet")
    try:
        cloud.command("storage", "buckets", "add-iam-policy-binding", "gs://" + BUCKET, *args)
        yield
    finally:
        cloud.command("storage", "buckets", "remove-iam-policy-binding", "gs://" + BUCKET, *args)
        policy = cloud.command("storage", "buckets", "get-iam-policy", "gs://" + BUCKET,
                               "--project=" + PROJECT, "--format=json", json_output=True)
        if not isinstance(policy, dict) or not isinstance(policy.get("bindings", []), list):
            raise ValueError("source-read access removal unconfirmed")
        for binding in policy.get("bindings", []):
            if not isinstance(binding, dict) or not isinstance(binding.get("members", []), list):
                raise ValueError("source-read policy unavailable")
            if (binding.get("role") == "roles/storage.objectViewer"
                    and "serviceAccount:" + GEN_SA in binding.get("members", [])
                    and binding.get("condition", {}).get("expression") == expression):
                raise ValueError("temporary source-read access remains")


class Source(schemas.Model):
    intake: schemas.Intake
    object_name: str
    generation: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    byte_length: StrictInt = Field(gt=0, le=evidence.MAX_BYTES)

    @model_validator(mode="after")
    def pdf_only(self):
        if self.intake.media_type != "application/pdf":
            raise ValueError("this deployment accepts original PDF evidence only")
        return self

    def bind(self, manifest):
        if self.object_name != "official-evidence-rollouts/" + manifest.rollout_id + "/source.pdf":
            raise ValueError("source object must belong to this exact rollout")
        if not set(self.intake.codes) <= set(manifest.cohort):
            raise ValueError("source codes outside declared rollout cohort")
        return fingerprint(self.model_dump(mode="json"))


def source_request(manifest, environment):
    request = Source.model_validate_json(environment["ENRICHMENT_SOURCE"])
    if request.bind(manifest) != environment["ENRICHMENT_SOURCE_HASH"]:
        raise ValueError("source approval binding differs")
    return request


def pinned_dsn(manifest, environment, role):
    dsn = environment["MFDATAINDIA_DSN"]
    parsed = conninfo_to_dict(dsn)
    if (
        environment["DEPLOYMENT_GIT_SHA"] != manifest.git_sha
        or parsed.get("host") != "/cloudsql/" + CONNECTION
        or parsed.get("dbname") != "mfdataindia"
        or parsed.get("user") != role
        or not parsed.get("password") or parsed.get("hostaddr")
    ):
        raise ValueError("exact DEV deployment and direct principal required")
    return dsn


def download(source, session):
    """Fixed Google endpoint, immutable generation, bounded original bytes; no AMC fetch."""
    session.trust_env = False
    identity = session.get(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"}, timeout=10, allow_redirects=False,
    )
    if identity.status_code != 200 or not isinstance(identity.json().get("access_token"), str):
        raise ValueError("workload storage credentials unavailable")
    token = identity.json()["access_token"]
    url = "https://storage.googleapis.com/storage/v1/b/" + BUCKET + "/o/" + quote(source.object_name, safe="")
    start = time.monotonic()
    with session.get(url, params={"alt": "media", "generation": source.generation},
                     headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity"},
                     stream=True, timeout=(10, 30), allow_redirects=False) as response:
        if (response.status_code != 200
                or response.headers.get("Content-Encoding", "identity") != "identity"
                or response.headers.get("x-goog-generation") != source.generation
                or response.headers.get("Content-Length") != str(source.byte_length)):
            raise ValueError("immutable source transport/generation differs")
        content = bytearray()
        for chunk in response.iter_content(65536):
            content.extend(chunk)
            if len(content) > source.byte_length or time.monotonic() - start > 60:
                raise ValueError("source transport exceeded bound")
    raw = bytes(content)
    if (len(raw) != source.byte_length or not raw.startswith(b"%PDF-")
            or hashlib.sha256(raw).hexdigest() != source.intake.expected_sha256):
        raise ValueError("original source size/type/checksum differs")
    return raw


def execute(manifest, environment, operation, session=None, *, root=Path("/app")):
    if operation not in ("install", "import"):
        raise ValueError("unsupported evidence deployment operation")
    role = "mfdata_admin" if operation == "install" else store.ROLES[0]
    dsn = pinned_dsn(manifest, environment, role)
    request = source_request(manifest, environment) if operation == "import" else None
    content = download(request, session) if request else None
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL " + ("REPEATABLE READ" if operation == "install" else "READ COMMITTED"))
        if operation == "install":
            result = operations.install(conn, root)
        else:
            conn.execute("SET LOCAL statement_timeout='120s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            acquire_nav_data_operation_lock(conn)
            verify_runtime(conn, "generator")
            store.verify_acl(conn)
            for code in request.intake.codes:
                if evidence.normalized(store.identity(conn, code)["amc"]) != evidence.normalized(request.intake.amc):
                    raise ValueError("document AMC differs from declared existing fund identity")
            result = store.append(conn, request.intake, content)
            result = {
                "document_id": result["document"]["document_id"],
                "content_hash": result["document"]["content_hash"],
                "mappings": [{"code": m["code"], "state": m["state"], "reason": m["reason"]} for m in result["mappings"]],
                "source_binding_hash": request.bind(manifest),
                "accepted_facts_published": 0,
                "source_rows_changed": 0,
            }
    return seal_evidence(manifest, "enrichment-" + operation, {"level": "DEV_DATABASE", **result})
