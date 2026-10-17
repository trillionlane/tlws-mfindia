"""Bounded English UTI task, immutable originals and atomic complete-cohort runs."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import re
import time

import psycopg
from psycopg.rows import dict_row
import requests

from mfdataindia.enrichment import (
    amfi_summary_identity,
    book_runtime,
    book_sources,
    book_store,
    layout_book_archive,
    sbi_acquisition,
    sbi_book,
    uti_acquisition,
    uti_batch,
    uti_book as book,
    uti_book_pdf,
)
from mfdataindia.review.policy import fingerprint


def retain(session, raw, *, media_type):
    return layout_book_archive.retain(session, raw, source_key=book.KEY, media_type=media_type)


def candidates(directory, identities, extractions):
    names = {
        name
        for extraction in extractions
        for detail in book.detail_pages(extraction)
        for name in detail["names"]
    }
    names |= {
        book.normalize(re.split(r"\s*-\s*(?:Regular|Direct)\b", i["scheme_name"], flags=re.I)[0])
        for i in identities
    }
    rows = [r for r in directory if book.normalize(r["scheme_name"]) in names]
    if not 1 <= len(rows) <= 200:
        raise ValueError("bounded complete UTI identity discovery cohort required")
    return sorted(rows, key=lambda r: int(r["scheme_id"]))


def run(
    dsn,
    execution,
    *,
    now=None,
    transport=uti_acquisition.fetch,
    extractor=uti_book_pdf.extract,
    archive=retain,
):
    observed = now or datetime.now(timezone.utc)
    spec = book_sources.specification(book.KEY)
    started = time.monotonic()
    phase = "scope"
    originals = []
    archives = []
    availability = []
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        book_store.verify_acl(conn)
        if (
            book_store.installed_configuration(conn)["document"]["source_specifications"].get(
                book.KEY
            )
            != spec
        ):
            raise ValueError("reviewed UTI authority required")
        previous = book_runtime.receipt(conn, book.KEY, execution)
        if previous:
            return previous
        identities = book_store.population(conn, book.KEY)

    def deadline():
        if time.monotonic() - started > 520:
            raise TimeoutError("UTI task deadline")

    def keep(original):
        deadline()
        same = [o for o in originals if o["source_url"] == original["source_url"]]
        if same:
            if len(same) != 1 or same[0] != original:
                raise ValueError("one consistent actual original per acquired URL required")
            return hashlib.sha256(original["content"]).hexdigest()
        originals.append(original)
        if sum(len(o["content"]) for o in originals) > spec["maximum_total_original_bytes"]:
            raise ValueError("UTI total original byte bound exceeded")
        return hashlib.sha256(original["content"]).hexdigest()

    try:
        phase = "English_catalogues"
        groups = []
        for request in uti_acquisition.recent_catalogues(observed):
            catalogue = transport(request["source_url"])
            catalogue_hash = keep(catalogue)
            selected = uti_acquisition.catalogue_books(catalogue["content"], request["source_url"])
            monthly = []
            for row in selected:
                phase = "English_books"
                original = transport(row["source_url"])
                keep(original)
                extraction = extractor(original["content"])
                deadline()
                monthly.append({**row, "catalogue_hash": catalogue_hash, "extraction": extraction})
            actual = book.period([r["extraction"] for r in monthly], observed)
            if actual != request["expected_period"]:
                raise ValueError(
                    "actual English book period differs; keep prior publication, never restamp"
                )
            groups.append({"as_of": actual, "monthly": monthly})
        if len({g["as_of"] for g in groups}) != 2:
            raise ValueError("two actual distinct complete periods required")
        phase = "directory"
        original = transport(book.DIRECTORY)
        directory_hash = keep(original)
        directory = sbi_book.directory(original["content"])
        requested = candidates(
            directory, identities, [r["extraction"] for g in groups for r in g["monthly"]]
        )
        phase = "identities"

        def family(row):
            deadline()
            sid = row["scheme_id"]
            documents = []
            bridges = []
            issue = None
            stage = "index"
            try:
                index = transport("https://www.amfiindia.com/api/schemes/" + sid + "/documents")
                documents.append(index)
                url = sbi_acquisition.identity_url(index["content"], sid)
                stage = "xml"
                deadline()
                original = transport(url)
                documents.append(original)
                stage = "xml_identity"
                bridges = amfi_summary_identity.rows(
                    original["content"], url, hashlib.sha256(index["content"]).hexdigest()
                )
                if not any(r["isin"] for r in bridges):
                    issue = {
                        "scheme_id": sid,
                        "phase": stage,
                        "reason": "unsupported_or_unpaired_xml_identity",
                    }
            except Exception as error:
                issue = {
                    "scheme_id": sid,
                    "phase": stage,
                    "reason": "ordinary_source_unavailable_or_invalid",
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
            return sid, documents, bridges, issue

        results = []
        with ThreadPoolExecutor(max_workers=spec["identity_workers"]) as pool:
            for future in as_completed([pool.submit(family, row) for row in requested]):
                deadline()
                results.append(future.result())
        bridges = []
        for sid, documents, rows, issue in sorted(results, key=lambda r: int(r[0])):
            for original in documents:
                keep(original)
            bridges.extend(rows)
            if issue:
                availability.append(issue)
        phase = "archive"
        seen = set()
        with requests.Session() as session:
            session.trust_env = False
            for original in originals:
                deadline()
                checksum = hashlib.sha256(original["content"]).hexdigest()
                if (
                    original["media_type"] in ("application/pdf", "application/xml")
                    and checksum not in seen
                ):
                    archives.append(
                        archive(session, original["content"], media_type=original["media_type"])
                    )
                    seen.add(checksum)
        phase = "compile"
        batches = [
            uti_batch.compile_batch(
                identities,
                g["monthly"],
                bridges,
                originals,
                as_of=g["as_of"],
                directory_hash=directory_hash,
            )
            for g in sorted(groups, key=lambda g: g["as_of"], reverse=True)
        ]
        phase = "publication"
        deadline()
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("SET LOCAL statement_timeout='30s'")
            conn.execute("SET LOCAL transaction_timeout='60s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            return book_runtime.publish(
                conn,
                book.KEY,
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
            previous = book_runtime.receipt(conn, book.KEY, execution)
            if previous:
                return previous
            return book_store.append_run(
                conn,
                {
                    "format": "official-amc-book-run-v1",
                    "source_key": book.KEY,
                    "execution_id": execution,
                    "observed_at": observed.isoformat(),
                    "status": "failed",
                    "phase": phase,
                    "error_type": type(error).__name__
                    if type(error).__name__
                    in (
                        "ValueError",
                        "TimeoutError",
                        "OperationalError",
                        "QueryCanceled",
                        "InsufficientPrivilege",
                    )
                    else "SourceError",
                    "specification_hash": fingerprint(spec),
                    "batch_ids": [],
                    "archives": archives,
                    "availability": availability,
                    "source_rows_changed": 0,
                    "all_amc_data_complete": False,
                },
            )
