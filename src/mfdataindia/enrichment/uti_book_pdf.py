"""Separate bounded UTI coordinate evidence; frozen layout decoders unchanged."""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from mfdataindia.enrichment import layout_book_pdf
from mfdataindia.review.policy import fingerprint


def policy():
    return {
        **layout_book_pdf.policy("uti"),
        "id": "official-uti-physical-field-columns",
        "version": 1,
        "column_anchor": "one physical Portfolio as on text span, between 25 and 55 percent of page width",
        "column_boundary": "two anchor-font em gutter left of Portfolio anchor; only unrotated physical text starts left of this boundary and below its baseline",
        "baseline_tolerance_points": 0.75,
        "ambiguity": "any multi-line callback in the selected physical region rejects that page column; no guessed layout-text fallback",
        "ordering": "physical baselines descending, then x ascending; bounded original fragment whitespace collapsed only within lines",
        "maximum_page_spans": 20000,
        "maximum_geometry_output_bytes": 33554432,
        "worker_wall_seconds": 180,
        "whole_document_semantic_coverage": False,
    }


def engine_hash():
    files = [
        Path(__file__),
        Path(__file__).with_name("_uti_book_pdf_worker.py"),
        Path(layout_book_pdf.__file__),
        Path(layout_book_pdf.__file__).with_name("_layout_book_pdf_worker.py"),
    ]
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


def extract(raw):
    started = time.monotonic()
    limits = policy()
    full = layout_book_pdf.extract(raw, profile="uti")
    remaining = limits["worker_wall_seconds"] - (time.monotonic() - started)
    if remaining <= 0:
        raise ValueError("UTI total decoding deadline exceeded")
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-m", "mfdataindia.enrichment._uti_book_pdf_worker"],
            input=raw,
            capture_output=True,
            timeout=remaining,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("UTI physical column deadline exceeded") from None
    if result.returncode or len(result.stdout) > limits["maximum_geometry_output_bytes"]:
        raise ValueError("UTI physical column decoding failed")
    columns = json.loads(result.stdout)
    if not isinstance(columns, list) or len(columns) != len(full["pages"]):
        raise ValueError("physical page inventory differs")
    pages = []
    for page, column in zip(full["pages"], columns, strict=True):
        if column is not None:
            if (
                set(column) != {"text", "geometry"}
                or not isinstance(column["text"], str)
                or len(column["text"]) > limits["maximum_page_characters"]
                or not isinstance(column["geometry"], dict)
            ):
                raise ValueError("bounded physical column required")
            geometry = column["geometry"]
            if not (
                0 < geometry["boundary_x"] < geometry["anchor_x"]
                and geometry["gutter_em"] == 2
                and geometry["baseline_tolerance_points"] == 0.75
            ):
                raise ValueError("physical region policy differs")
        text = column["text"] if column is not None else None
        pages.append(
            {
                **page,
                "physical_left_text": text,
                "physical_left_hash": hashlib.sha256(text.encode()).hexdigest()
                if text is not None
                else None,
                "physical_geometry": column["geometry"] if column is not None else None,
            }
        )
    body = {
        **{k: v for k, v in full.items() if k not in ("pages", "extraction_hash")},
        "policy_hash": fingerprint(limits),
        "engine_hash": engine_hash(),
        "pages": pages,
    }
    return {**body, "extraction_hash": fingerprint(body)}
