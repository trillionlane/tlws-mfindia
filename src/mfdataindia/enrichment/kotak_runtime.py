"""Bounded independently receipted Kotak task, additive and replay safe."""

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
    kotak_acquisition,
    kotak_batch,
    kotak_book,
    layout_book_archive,
    layout_book_pdf,
    sbi_acquisition,
    sbi_book,
)
from mfdataindia.review.policy import fingerprint


def decode(raw):
    return layout_book_pdf.extract(raw, profile="kotak")


def retain(session, raw, *, media_type):
    return layout_book_archive.retain(
        session, raw, source_key=kotak_book.KEY, media_type=media_type
    )


def candidates(directory, identities, extracts):
    headings = {
        name
        for extraction in extracts
        for detail in kotak_book.detail_pages(extraction)
        for name in detail["names"]
    }
    names = {
        kotak_book.normalize(
            re.split(r"\s+-\s+(?:Regular|Direct)\b", i["scheme_name"], flags=re.I)[0]
        )
        for i in identities
    }
    result = [
        row for row in directory if kotak_book.normalize(row["scheme_name"]) in names | headings
    ]
    if not 1 <= len(result) <= 200:
        raise ValueError("bounded complete identity discovery cohort required")
    return sorted(result, key=lambda r: int(r["scheme_id"]))


def run(
    dsn, execution, *, now=None, transport=kotak_acquisition.fetch, extractor=decode, archive=retain
):
    observed = now or datetime.now(timezone.utc)
    spec = book_sources.specification(kotak_book.KEY)
    started = time.monotonic()
    phase, originals, archives, availability = "scope", [], [], []
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        book_store.verify_acl(conn)
        if (
            book_store.installed_configuration(conn)["document"]["source_specifications"].get(
                kotak_book.KEY
            )
            != spec
        ):
            raise ValueError("reviewed Kotak authority must be installed")
        previous = book_runtime.receipt(conn, kotak_book.KEY, execution)
        if previous:
            return previous
        identities = book_store.population(conn, kotak_book.KEY)

    def deadline():
        if time.monotonic() - started > 520:
            raise TimeoutError("Kotak bounded source task deadline")

    def kept(original):
        deadline()
        if any(o["source_url"] == original["source_url"] for o in originals):
            raise ValueError("one original per acquired URL required")
        originals.append(original)
        if sum(len(o["content"]) for o in originals) > spec["maximum_total_original_bytes"]:
            raise ValueError("Kotak aggregate original byte bound exceeded")
        return hashlib.sha256(original["content"]).hexdigest()

    try:
        phase = "catalogues"
        expected = kotak_acquisition.recent_catalogues(observed)
        monthly = []
        for request in expected:
            try:
                original = transport(request["source_url"])
                kept(original)
                url = kotak_acquisition.catalogue_book(original["content"], original["source_url"])
            except Exception as error:
                availability.append(
                    {
                        "expected_period": request["expected_period"],
                        "phase": "catalogue",
                        "reason": "ordinary_source_unavailable_or_invalid",
                        "error_type": type(error).__name__
                        if type(error).__name__ in ("ValueError", "TimeoutError", "OSError")
                        else "SourceError",
                    }
                )
                continue
            phase = "books"
            original = transport(url)
            kept(original)
            extraction = extractor(original["content"])
            as_of = kotak_book.period(extraction, observed)
            monthly.append({"source_url": url, "extraction": extraction, "as_of": as_of})
            if as_of != request["expected_period"]:
                availability.append(
                    {
                        "phase": "reporting_date",
                        "expected_period": request["expected_period"],
                        "actual_period": as_of,
                        "reason": "actual_source_date_retained_not_catalogue_or_filename_date",
                    }
                )
        if len({m["as_of"] for m in monthly}) < 2:
            phase = "reviewed_historical_source"
            original = transport(kotak_book.HISTORICAL)
            kept(original)
            if (
                hashlib.sha256(original["content"]).hexdigest()
                != spec["reviewed_historical"]["sha256"]
            ):
                raise ValueError("reviewed historical original changed; retain prior publication")
            extraction = extractor(original["content"])
            as_of = kotak_book.period(extraction, observed)
            if as_of != spec["reviewed_historical"]["actual_reporting_period"]:
                raise ValueError(
                    "reviewed historical disclosure changed; retain it for separate review"
                )
            monthly.append(
                {"source_url": original["source_url"], "extraction": extraction, "as_of": as_of}
            )
        if len(monthly) != 2 or len({m["as_of"] for m in monthly}) != 2:
            raise ValueError("two actual distinct available reporting periods required")
        monthly.sort(key=lambda m: m["as_of"], reverse=True)
        actual_dates = {m["as_of"] for m in monthly}
        for request in expected:
            if request["expected_period"] not in actual_dates:
                availability.append(
                    {
                        "phase": "period_coverage",
                        "expected_period": request["expected_period"],
                        "reason": "expected_recent_period_not_acquired",
                        "not_proof_of_issuer_absence": True,
                    }
                )
        phase = "directory"
        original = transport(kotak_book.DIRECTORY)
        directory_hash = kept(original)
        directory = sbi_book.directory(original["content"])
        requested = candidates(directory, identities, [m["extraction"] for m in monthly])
        phase = "identities"

        def family(row):
            deadline()
            sid = row["scheme_id"]
            documents, bridges, issue = [], [], None
            stage = "index"
            try:
                index = transport("https://www.amfiindia.com/api/schemes/" + sid + "/documents")
                documents.append(index)
                url = sbi_acquisition.identity_url(index["content"], sid)
                stage = "xml"
                original = transport(url)
                documents.append(original)
                stage = "xml_identity"
                bridges = amfi_summary_identity.rows(
                    original["content"], url, hashlib.sha256(index["content"]).hexdigest()
                )
                if not bridges:
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
                kept(original)
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
            kotak_batch.compile_batch(
                identities,
                item,
                bridges,
                originals,
                as_of=item["as_of"],
                directory_hash=directory_hash,
                expected_periods=[r["expected_period"] for r in expected],
            )
            for item in monthly
        ]
        phase = "publication"
        deadline()
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("SET LOCAL statement_timeout='30s'")
            conn.execute("SET LOCAL transaction_timeout='60s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            return book_runtime.publish(
                conn,
                kotak_book.KEY,
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
            previous = book_runtime.receipt(conn, kotak_book.KEY, execution)
            if previous:
                return previous
            return book_store.append_run(
                conn,
                {
                    "format": "official-amc-book-run-v1",
                    "source_key": kotak_book.KEY,
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
