"""Enrich NULL beta/Sharpe/std-dev from AMC monthly factsheet PDFs.

POC scope: ABSL (Aditya Birla Sun Life) — the parser is pluggable per AMC
(``mfdataindia.ingest.amc_factsheets``), so more AMCs join as new parser
classes; the loader is AMC-agnostic.

    PYTHONPATH=src python scripts/enrich_amc_factsheets.py                 # latest ABSL month, all funds
    PYTHONPATH=src python scripts/enrich_amc_factsheets.py --dry-run      # report only, no writes
    PYTHONPATH=src python scripts/enrich_amc_factsheets.py --pdf file.pdf # local file (no download)
    PYTHONPATH=src python scripts/enrich_amc_factsheets.py --year 2026    # specific year (latest month of it)

Fill-if-missing only: a fund field that already has a value is never
touched. Every fill is logged in mf.factsheet_fields_log (AMC, document
period, NAV as-of date, PDF page, URL) and the fetch in source_metadata.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pypdf  # noqa: E402

from mfdataindia.ingest.amc_factsheets import (  # noqa: E402
    AbSLFactsheetClient,
    AbSLParser,
)
from mfdataindia.load.amc_factsheets_to_store import load_factsheets  # noqa: E402
from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402


def _candidates(store: PostgresStore) -> dict[str, int]:
    """In-scope live Regular GROWTH variants: name -> code."""
    with store.connect().cursor() as cur:
        return {
            r["scheme_name"]: int(r["amfi_scheme_code"])
            for r in cur.execute(
                "SELECT amfi_scheme_code, scheme_name FROM mf.funds "
                "WHERE in_scope AND NOT is_defunct AND is_active "
                "AND plan_type = 'REGULAR' AND option_type = 'GROWTH'")
        }


def _extract_pages(pdf_bytes: bytes) -> list[str]:
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return [p.extract_text() or "" for p in reader.pages]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None,
                    help="target DSN (default: $MFDATAINDIA_DSN); required -- there is "
                         "no built-in default")
    ap.add_argument("--amc", default="absl", choices=["absl"],
                    help="AMC to enrich (parsers are per-AMC; absl first)")
    ap.add_argument("--pdf", default=None,
                    help="local factsheet PDF (skips the download step)")
    ap.add_argument("--year", type=int, default=None,
                    help="factsheet year to use (default: current year)")
    ap.add_argument("--period", default=None,
                    help="factsheet period label for --pdf runs "
                         "(e.g. 'September 2026'); default 'local-file'")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute and report what would be filled; write nothing")
    args = ap.parse_args()

    try:
        dsn = resolve_dsn(args.dsn, purpose="AMC factsheet enrichment")
    except DsnError as exc:
        ap.error(str(exc))
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("enrich_amc_factsheets")
    log.info("target database: %s", describe_dsn(dsn))

    if args.pdf:
        pdf_bytes = Path(args.pdf).read_bytes()
        period = args.period or "local-file"
        url: str | None = None
        log.info("using local PDF %s (period=%s)", args.pdf, period)
    else:
        import datetime
        client = AbSLFactsheetClient()
        year = args.year or datetime.date.today().year
        doc = client.latest(year)
        period, url = doc.period, doc.url
        log.info("downloading %s: %s", period, url)
        pdf_bytes = client.download(doc)

    pages = _extract_pages(pdf_bytes)
    log.info("extracted %d pages", len(pages))
    parser = AbSLParser(period)
    factsheets = list(parser.parse_pages(pages))
    log.info("parsed %d schemes from factsheet", len(factsheets))

    use_copy = os.environ.get("MF_TEST_NO_COPY") is None
    store = PostgresStore(dsn, use_copy=use_copy)
    with store:
        candidates = _candidates(store)
        rep = load_factsheets(store, factsheets, candidates,
                              factsheet_url=url, dry_run=args.dry_run)
        if not args.dry_run:
            store.record_fetch({
                "source": "AMC", "endpoint": url or "local-file",
                "entity_kind": "FACTSHEET",
                "entity_key": f"{args.amc}:{period}",
                "http_status": None, "content_type": "application/pdf",
                "content_bytes": len(pdf_bytes),
                "acquisition": "LIVE" if url else "USER_PROVIDED",
                "records_in": len(factsheets),
                "records_ok": rep["matched"],
                "notes": {"dry_run": False, "report": rep},
            })
            store.refresh_dataset_summary("amc_factsheet_enrichment")
    print("FACTSHEET REPORT:", rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
