"""Independent registered source tasks; immutable full-population publication."""

import hashlib
import json
import re
from datetime import datetime, timezone

import psycopg
import requests
from psycopg.rows import dict_row

from mfdataindia.enrichment import (
    acquisition,
    amc_book_pdf,
    book_acquisition,
    book_facts,
    book_sources,
    book_store,
    multi_facts,
    multi_runtime,
    refresh_acquisition,
    refresh_archive,
)
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.store.locks import acquire_nav_data_operation_lock


def receipt(conn, source_key, execution):
    row = conn.execute(
        "SELECT document FROM mf_book_facts.runs WHERE source_key=%s AND execution_id=%s",
        (source_key, execution),
    ).fetchone()
    return row["document"] if row else None


def fetch_identity(spec):
    if len(spec["identity_documents"]) != 1:
        raise ValueError("one reviewed issuer identity document required")
    source = spec["identity_documents"][0]
    result = acquisition._fetch(
        source["url"],
        {"nsearchives.nseindia.com"},
        timeout=30,
        maximum=source["maximum_bytes"],
        media_types=("application/pdf",),
        allow_redirects=False,
    )
    if hashlib.sha256(result["content"]).hexdigest() != source["sha256"]:
        raise ValueError("issuer identity original differs from reviewed pin")
    return {**result, "source_url": source["url"]}


def retain_source(conn, key, original):
    raw, url = original["content"], original["source_url"]
    digest = hashlib.sha256(raw).hexdigest()
    media = original["media_type"]
    conn.execute(
        "INSERT INTO mf_book_facts.sources VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(source_key,content_hash) DO NOTHING",
        (key, digest, url, media, len(raw), raw),
    )
    row = conn.execute(
        "SELECT source_url,media_type,byte_length,encode(sha256(content),'hex') AS checksum FROM mf_book_facts.sources WHERE source_key=%s AND content_hash=%s",
        (key, digest),
    ).fetchone()
    expected = {"source_url": url, "media_type": media, "byte_length": len(raw), "checksum": digest}
    if key in ("sbi-expanded", "kotak-expanded", "uti-expanded"):
        # BEFORE INSERT has independently checked this acquired alias URL and
        # its retained parent index/catalogue even when ON CONFLICT skips the
        # content-addressed row. Preserve its FIRST source_url; all actual URL
        # edges remain sealed in v2 batch document_refs, never an UPDATE.
        expected["source_url"] = row["source_url"]
    if row != expected:
        raise ValueError("retained whole-AMC original replay differs")
    return digest


def publish(
    conn, key, execution, observed, identities, batches, originals, archives, *, availability=None
):
    verify_runtime(conn, "generator")
    book_store.verify_acl(conn)
    acquire_nav_data_operation_lock(conn)
    previous = receipt(conn, key, execution)
    if previous:
        return previous
    if book_store.population(conn, key) != identities:
        raise ValueError(
            "current population changed during extraction; preserve last good publication"
        )
    catalog_hashes = []
    for original in originals:
        digest = retain_source(conn, key, original)
        if (
            original["media_type"] in ("text/html", "application/json")
            and digest not in catalog_hashes
        ):
            catalog_hashes.append(digest)
    for batch in batches:
        if batch["population_hash"] != fingerprint(identities):
            raise ValueError("compiled complete population differs")
        book_store.append_exact(
            conn,
            "batches",
            "batch_id",
            batch,
            {
                k: batch[k]
                for k in ("source_key", "content_hash", "identity_content_hash", "disclosure_as_of")
            },
        )
    batch_ids = [
        b["batch_id"]
        for b in sorted(batches, key=lambda b: (b["disclosure_as_of"], b["batch_id"]), reverse=True)
    ]
    existing = conn.execute(
        "SELECT 1 FROM mf_book_facts.runs WHERE source_key=%s AND status<>'failed' AND document->'batch_ids'=%s::jsonb LIMIT 1",
        (key, json.dumps(batch_ids)),
    ).fetchone()
    dataset = conn.execute(
        "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
    ).fetchone()
    return book_store.append_run(
        conn,
        {
            "format": "official-amc-book-run-v1",
            "source_key": key,
            "execution_id": execution,
            "observed_at": observed.isoformat(),
            "status": "unchanged" if existing else "updated",
            "specification_hash": fingerprint(book_sources.specification(key)),
            "population_hash": fingerprint(identities),
            "dataset_version": dataset["dataset_version"],
            "batch_ids": batch_ids,
            "catalog_hashes": catalog_hashes,
            "archives": archives,
            "coverage": [book_facts.coverage(b) for b in batches],
            "source_rows_changed": 0,
            "all_amc_data_complete": False,
            "verification_status": "needs_verification",
            "adjusted_idcw_analytics_enabled": False,
            **({"availability": availability} if availability is not None else {}),
        },
    )


def absl(
    dsn,
    execution,
    *,
    now=None,
    transport=refresh_acquisition.fetch,
    identity_transport=fetch_identity,
    extractor=amc_book_pdf.extract,
    archive=refresh_archive._retain,
):
    key, observed = "absl-expanded", now or datetime.now(timezone.utc)
    spec = book_sources.specification(key)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        book_store.verify_acl(conn)
        previous = receipt(conn, key, execution)
        if previous:
            return previous
        identities = book_store.population(conn, key)
    phase, archives = "acquisition", []
    try:
        catalogs, editions = book_acquisition.discover_absl(observed, transport=transport)
        identity = identity_transport(spec)
        originals = [*catalogs, identity]
        monthly = []
        for edition in editions:
            original = transport(edition["url"], book=True)
            originals.append(original)
            monthly.append((edition, original))
        phase = "archive"
        with requests.Session() as session:
            session.trust_env = False
            for original in originals:
                if original["media_type"] == "application/pdf":
                    archives.append(
                        archive(session, original["content"], prefix=spec["archive_prefix"])
                    )
        phase = "extraction"
        summary = extractor(identity["content"])
        batches = [
            book_facts.compile_batch(
                key,
                identities,
                extractor(original["content"]),
                summary,
                as_of=edition["as_of"],
                source_url=edition["url"],
            )
            for edition, original in monthly
        ]
        phase = "publication"
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("SET LOCAL statement_timeout='30s'")
            conn.execute("SET LOCAL transaction_timeout='60s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            return publish(conn, key, execution, observed, identities, batches, originals, archives)
    except Exception as error:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            previous = receipt(conn, key, execution)
            if previous:
                return previous
            return book_store.append_run(
                conn,
                {
                    "format": "official-amc-book-run-v1",
                    "source_key": key,
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
                    "specification_hash": fingerprint(spec),
                    "batch_ids": [],
                    "archives": archives,
                    "source_rows_changed": 0,
                    "all_amc_data_complete": False,
                },
            )


def execute(dsn, execution, *, task_index):
    registry = book_sources.registry()
    if (
        not re.fullmatch(re.escape(multi_facts.JOB) + r"-[a-z0-9-]{1,40}", execution)
        or type(task_index) is not int
        or not 0 <= task_index < registry["maximum_sources_per_execution"]
    ):
        raise ValueError("registered execution and task index required")
    if task_index < 2:
        return multi_runtime.execute(dsn, execution, task_index=task_index)
    source = registry["sources"][task_index - 2]
    from mfdataindia.enrichment import sbi_runtime, kotak_runtime, uti_runtime, uti_runtime_v2, uti_runtime_v3

    result = {
        "absl-full-book-v1": absl,
        "sbi-full-book-v1": sbi_runtime.run,
        "kotak-monthly-book-v1": kotak_runtime.run,
        "uti-english-monthly-book-v1": uti_runtime.run,
        "uti-english-monthly-book-v2": uti_runtime_v2.run,
        "uti-english-monthly-book-v3": uti_runtime_v3.run,
    }[source["adapter"]](dsn, execution)
    return {
        "format": "official-amc-book-task-v1",
        "execution_id": execution,
        "task_index": task_index,
        "source_key": source["key"],
        "result": result,
        "status": "failed" if result["status"] == "failed" else "completed",
        "registry_hash": fingerprint(registry),
        "source_rows_changed": 0,
        "all_amc_data_complete": False,
    }
