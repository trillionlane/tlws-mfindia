"""Two registered source adapters with independent commits and immutable replay."""

import hashlib
import re
from datetime import datetime, timezone

import psycopg
import requests
from psycopg.rows import dict_row

from mfdataindia.enrichment import acquisition, multi_facts, multi_store, hdfc_monthly
from mfdataindia.enrichment import refresh_runtime, refresh_archive, store as evidence
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.store.locks import acquire_nav_data_operation_lock


def fetch(url):
    allowed = {multi_facts.source()[role]["url"] for role in ("edition", "identity_source")}
    if url not in allowed:
        raise ValueError("registered source URL required")

    def validate(value, hosts):
        if value != url:
            raise ValueError("registered source redirects are not permitted")
        return acquisition.public_url(value, hosts)

    return acquisition._fetch(
        url,
        {"files.hdfcfund.com"},
        timeout=30,
        maximum=8 * 1024 * 1024,
        validate=validate,
        media_types=("application/pdf",),
    )


def receipt(conn, execution):
    row = conn.execute(
        "SELECT document FROM mf_multi_facts.runs WHERE execution_id=%s", (execution,)
    ).fetchone()
    return row["document"] if row else None


def publish(conn, execution, observed, originals, archives):
    verify_runtime(conn, "generator")
    multi_store.verify_acl(conn)
    acquire_nav_data_operation_lock(conn)
    previous = receipt(conn, execution)
    if previous:
        return previous
    identity = evidence.identity(conn, 101763)
    snapshot = hdfc_monthly.derive(identity, originals["edition"], originals["identity_source"])
    source = multi_facts.source()
    for role, content in originals.items():
        checksum = hashlib.sha256(content).hexdigest()
        conn.execute(
            "INSERT INTO mf_multi_facts.sources VALUES(%s,%s,%s,%s) ON CONFLICT(content_hash) DO NOTHING",
            (checksum, source[role]["url"], len(content), content),
        )
        retained = conn.execute(
            "SELECT source_url,byte_length,encode(sha256(content),'hex') AS checksum FROM mf_multi_facts.sources WHERE content_hash=%s",
            (checksum,),
        ).fetchone()
        if retained != {
            "source_url": source[role]["url"],
            "byte_length": len(content),
            "checksum": checksum,
        }:
            raise ValueError("retained registered original replay differs")
    multi_store.append_exact(
        conn,
        "snapshots",
        "snapshot_id",
        snapshot,
        {
            "amfi_scheme_code": 101763,
            "content_hash": snapshot["content_hash"],
            "identity_content_hash": snapshot["identity_content_hash"],
            "disclosure_as_of": snapshot["disclosure_as_of"],
        },
    )
    existing = conn.execute(
        "SELECT 1 FROM mf_multi_facts.runs WHERE status<>'failed' AND document->'snapshot_ids' ? %s LIMIT 1",
        (snapshot["snapshot_id"],),
    ).fetchone()
    return multi_store.append_run(
        conn,
        {
            "format": "registered-facts-run-v1",
            "source_key": source["key"],
            "execution_id": execution,
            "observed_at": observed.isoformat(),
            "status": "unchanged" if existing else "updated",
            "policy_hash": fingerprint(multi_facts.policy()),
            "snapshot_ids": [snapshot["snapshot_id"]],
            "archives": archives,
            "source_rows_changed": 0,
            "all_amc_data_complete": False,
            "verification_status": "needs_verification",
            "discovery": source["discovery"],
        },
    )


def hdfc(dsn, execution, *, now=None, transport=fetch, archive=refresh_archive.retain_registered):
    observed = now or datetime.now(timezone.utc)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        multi_store.verify_acl(conn)
        previous = receipt(conn, execution)
        if previous:
            return previous
    phase, archives = "acquisition", []
    try:
        originals = {}
        with requests.Session() as session:
            session.trust_env = False
            for role in ("edition", "identity_source"):
                phase = "acquisition"
                content = transport(multi_facts.source()[role]["url"])["content"]
                phase = "archive"
                archives.append(archive(session, content))
                originals[role] = content
        phase = "publication"
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("SET LOCAL statement_timeout='120s'")
            conn.execute("SET LOCAL transaction_timeout='180s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            return publish(conn, execution, observed, originals, archives)
    except Exception as error:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            previous = receipt(conn, execution)
            if previous:
                return previous
            return multi_store.append_run(
                conn,
                {
                    "format": "registered-facts-run-v1",
                    "source_key": "hdfc-flexi-cap",
                    "execution_id": execution,
                    "observed_at": observed.isoformat(),
                    "status": "failed",
                    "phase": phase,
                    "error_type": type(error).__name__
                    if type(error).__name__
                    in {
                        "ValueError",
                        "TimeoutError",
                        "OperationalError",
                        "QueryCanceled",
                        "InsufficientPrivilege",
                    }
                    else "SourceError",
                    "policy_hash": fingerprint(multi_facts.policy()),
                    "snapshot_ids": [],
                    "archives": archives,
                    "source_rows_changed": 0,
                    "all_amc_data_complete": False,
                },
            )


def execute(dsn, execution, *, task_index=None):
    if not re.fullmatch(re.escape(multi_facts.JOB) + r"-[a-z0-9-]{1,40}", execution):
        raise ValueError("single registered source execution required")
    sources = multi_facts.registry()["sources"]
    if task_index is not None:
        if type(task_index) is not int or not 0 <= task_index < len(sources):
            raise ValueError("registered task index required")
        sources = [sources[task_index]]
    results = []
    adapters = {
        "retained-absl-monthly-v1": refresh_runtime.execute,
        "hdfc-reviewed-monthly-v1": hdfc,
    }
    for source in sources:
        try:
            result = adapters[source["adapter"]](dsn, execution)
        except Exception:
            # A broken receipt/DB boundary must not suppress the other source.
            result = {
                "status": "failed",
                "error_type": "SourceBoundaryError",
                "receipt_confirmed": False,
            }
        results.append({"source_key": source["key"], "result": result})
    return {
        "format": "registered-facts-batch-v1",
        "execution_id": execution,
        "status": "failed"
        if any(r["result"]["status"] == "failed" for r in results)
        else "completed",
        "registry_hash": fingerprint(multi_facts.registry()),
        "task_index": task_index,
        "sources": results,
        "source_rows_changed": 0,
        "all_amc_data_complete": False,
    }
