"""Bounded SBI task, immutable originals, explicit family gaps, atomic receipt."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import time

import psycopg
from psycopg.rows import dict_row
import requests

from mfdataindia.enrichment import (
    amc_book_pdf,
    book_runtime,
    book_sources,
    book_store,
    sbi_acquisition,
    sbi_archive,
    sbi_batch,
    sbi_book,
)
from mfdataindia.review.policy import fingerprint


def run(
    dsn,
    execution,
    *,
    now=None,
    transport=sbi_acquisition.fetch,
    extractor=amc_book_pdf.extract,
    archive=sbi_archive.retain,
):
    observed = now or datetime.now(timezone.utc)
    spec = book_sources.specification(sbi_book.KEY)
    started = time.monotonic()
    originals, archives, availability = [], [], []
    phase = "scope"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        book_store.verify_acl(conn)
        installed = book_store.installed_configuration(conn)["document"]
        if installed["source_specifications"].get(sbi_book.KEY) != spec:
            raise ValueError("reviewed SBI authority must be installed before task execution")
        previous = book_runtime.receipt(conn, sbi_book.KEY, execution)
        if previous:
            return previous
        identities = book_store.population(conn, sbi_book.KEY)

    def deadline():
        if time.monotonic() - started > 520:
            raise TimeoutError("SBI source task bounded deadline")

    def retained(original):
        deadline()
        if any(o["source_url"] == original["source_url"] for o in originals):
            raise ValueError("one original per acquired source URL required")
        originals.append(original)
        if sum(len(o["content"]) for o in originals) > spec["maximum_total_original_bytes"]:
            raise ValueError("SBI total original byte bound exceeded")
        return hashlib.sha256(original["content"]).hexdigest()

    try:
        phase = "catalogue"
        catalog = transport(sbi_book.CATALOGUE)
        retained(catalog)
        editions = sbi_book.editions(catalog["content"], observed)
        directory = transport(sbi_book.DIRECTORY)
        directory_hash = retained(directory)
        families = sbi_book.directory(directory["content"])
        phase = "books"
        periods, extractions = [], []
        for edition in editions:
            monthly = {}
            for kind in ("active", "passive"):
                original = transport(edition["documents"][kind])
                retained(original)
                extraction = extractor(original["content"])
                extractions.append(extraction)
                monthly[kind] = {"source_url": original["source_url"], "extraction": extraction}
            periods.append((edition["as_of"], monthly))
        requested = sbi_book.candidates(families, identities, extractions)
        phase = "identities"

        def family(row):
            deadline()
            sid = row["scheme_id"]
            index_url = "https://www.amfiindia.com/api/schemes/" + sid + "/documents"
            documents, rows, issue = [], [], None
            stage = "index"
            try:
                index = transport(index_url)
                documents.append(index)
                xml_url = sbi_acquisition.identity_url(index["content"], sid)
                stage = "xml"
                original = transport(xml_url)
                documents.append(original)
                stage = "xml_identity"
                rows = sbi_book.identity_rows(
                    original["content"], xml_url, hashlib.sha256(index["content"]).hexdigest()
                )
                if not rows:
                    issue = {
                        "scheme_id": sid,
                        "phase": stage,
                        "reason": "unsupported_or_unpaired_xml_identity",
                    }
            except Exception as error:
                issue = {
                    "scheme_id": sid,
                    "phase": stage,
                    "reason": "source_unavailable_or_invalid",
                    "error_type": type(error).__name__
                    if type(error).__name__
                    in (
                        "ValueError",
                        "TimeoutError",
                        "OSError",
                        "ParseError",
                        "DTDForbidden",
                        "EntitiesForbidden",
                    )
                    else "SourceError",
                }
            return sid, documents, rows, issue

        results = []
        with ThreadPoolExecutor(max_workers=spec["identity_workers"]) as pool:
            for future in as_completed([pool.submit(family, row) for row in requested]):
                deadline()
                results.append(future.result())
        bridges = []
        for sid, documents, rows, issue in sorted(results, key=lambda r: int(r[0])):
            for original in documents:
                retained(original)
            bridges.extend(rows)
            if issue:
                availability.append(issue)
        phase = "archive"
        seen = set()
        with requests.Session() as session:
            session.trust_env = False
            for original in originals:
                deadline()
                digest = hashlib.sha256(original["content"]).hexdigest()
                if (
                    original["media_type"] in ("application/pdf", "application/xml")
                    and digest not in seen
                ):
                    archives.append(
                        archive(session, original["content"], media_type=original["media_type"])
                    )
                    seen.add(digest)
        phase = "compile"
        batches = [
            sbi_batch.compile_batch(
                identities, monthly, bridges, originals, as_of=as_of, directory_hash=directory_hash
            )
            for as_of, monthly in periods
        ]
        phase = "publication"
        deadline()
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("SET LOCAL statement_timeout='30s'")
            conn.execute("SET LOCAL transaction_timeout='60s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            return book_runtime.publish(
                conn,
                sbi_book.KEY,
                execution,
                observed,
                identities,
                batches,
                originals,
                archives,
                availability=availability,
            )
    except Exception as error:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            previous = book_runtime.receipt(conn, sbi_book.KEY, execution)
            if previous:
                return previous
            return book_store.append_run(
                conn,
                {
                    "format": "official-amc-book-run-v1",
                    "source_key": sbi_book.KEY,
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
                    "availability": availability,
                    "source_rows_changed": 0,
                    "all_amc_data_complete": False,
                },
            )
