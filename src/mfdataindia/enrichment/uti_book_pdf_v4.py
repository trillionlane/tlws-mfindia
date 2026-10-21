"""Frozen physical columns/titles plus complete Closing-AUM original rows."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from mfdataindia.enrichment import (
    layout_book_pdf_v2 as layout_book_pdf, pdf_security, uti_aum_row, uti_book_pdf_v2,
    uti_book_pdf_v3,
)
from mfdataindia.review.policy import fingerprint


def policy():
    return {**uti_book_pdf_v2.policy(), "version": 4,
            "dependency_contract": pdf_security.dependency_contract(),
            "retained_decoder_policy_hash": fingerprint(uti_book_pdf_v3.policy()),
            "retained_physical_policy_hash": fingerprint(uti_book_pdf_v2.policy()),
            "additional_aum_row_policy": uti_aum_row.policy(),
            "financial_field_rules_changed": False,
            "frozen_column_grouping_changed": False}


def engine_hash():
    files = [Path(__file__), Path(__file__).with_name("_uti_book_pdf_worker_v4.py"),
             Path(uti_book_pdf_v3.__file__),
             Path(__file__).with_name("_uti_book_pdf_worker_v3.py"), Path(pdf_security.__file__),
             Path(uti_aum_row.__file__), Path(uti_book_pdf_v2.__file__),
             Path(uti_book_pdf_v2.__file__).with_name("_uti_book_pdf_worker_v2.py"),
             Path(uti_book_pdf_v2.uti_book_pdf.__file__), Path(layout_book_pdf.__file__),
             Path(uti_book_pdf_v2.layout_book_pdf.__file__),
             Path(layout_book_pdf.__file__).with_name("_layout_book_pdf_worker_v2.py")]
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


def extract(raw):
    started = time.monotonic()
    pdf_security.require_runtime()
    limits = policy()
    full = layout_book_pdf.extract(raw, profile="uti")
    remaining = limits["worker_wall_seconds"] - (time.monotonic() - started)
    if remaining <= 0:
        raise ValueError("UTI total decoding deadline exceeded")
    try:
        result = subprocess.run([sys.executable, "-I", "-m", "mfdataindia.enrichment._uti_book_pdf_worker_v4"],
                                input=raw, capture_output=True, timeout=remaining, check=False)
    except subprocess.TimeoutExpired:
        raise ValueError("UTI original column/row deadline exceeded") from None
    if result.returncode or len(result.stdout) > limits["maximum_geometry_output_bytes"]:
        raise ValueError("UTI original column/row decoding failed")
    columns = json.loads(result.stdout)
    if not isinstance(columns, list) or len(columns) != len(full["pages"]):
        raise ValueError("physical page inventory differs")
    pages = []
    for page, column in zip(full["pages"], columns, strict=True):
        title, row = None, None
        if column is not None:
            if (set(column) != {"text", "geometry", "header", "aum_row"}
                    or not isinstance(column["text"], str)
                    or len(column["text"]) > limits["maximum_page_characters"]
                    or not isinstance(column["geometry"], dict)):
                raise ValueError("bounded original physical evidence required")
            geometry = column["geometry"]
            if not (0 < geometry["boundary_x"] < geometry["anchor_x"]
                    and geometry["gutter_em"] == 2 and geometry["baseline_tolerance_points"] == 0.75):
                raise ValueError("retained physical region policy differs")
            title = column["header"]
            if title is not None:
                if (not isinstance(title, dict)
                        or set(title) != {"text", "text_hash", "x", "y", "font_points"}
                        or not isinstance(title["text"], str)
                        or not 1 <= len(title["text"]) <= limits["physical_title_maximum_characters"]
                        or "\n" in title["text"] or "\r" in title["text"] or not title["text"].startswith("UTI ")
                        or title["text_hash"] != hashlib.sha256(title["text"].encode()).hexdigest()
                        or not 0 <= title["x"] < 0.1 * geometry["page_width"]
                        or not 0.9 * geometry["page_height"] <= title["y"] <= geometry["page_height"]
                        or not 8 <= title["font_points"] <= 30):
                    raise ValueError("unique original physical title policy differs")
            row = column["aum_row"]
            if row is not None:
                uti_aum_row.validate(row)
                if any(row[k] != uti_aum_row.token(geometry[k]) for k in ("page_width", "page_height", "boundary_x")):
                    raise ValueError("AUM evidence outside retained original region")
        text = column["text"] if column is not None else None
        pages.append({**page, "physical_left_text": text,
                      "physical_left_hash": hashlib.sha256(text.encode()).hexdigest() if text is not None else None,
                      "physical_geometry": column["geometry"] if column is not None else None,
                      "physical_title": title, "physical_aum_row": row})
    body = {**{k: v for k, v in full.items() if k not in ("pages", "extraction_hash")},
            "policy_hash": fingerprint(limits), "engine_hash": engine_hash(), "pages": pages}
    return {**body, "extraction_hash": fingerprint(body)}
