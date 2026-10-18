"""Bounded original row sidecar alongside the unchanged v2 physical column."""

import io
import json
import logging
import resource
import sys


def main():
    from mfdataindia.enrichment import uti_aum_row, uti_book_pdf_v3
    from mfdataindia.enrichment._uti_book_pdf_worker_v2 import column

    limits = uti_book_pdf_v3.policy()
    resource.setrlimit(resource.RLIMIT_CPU, (limits["worker_cpu_seconds"],) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (limits["linux_worker_address_space_bytes"],) * 2)
    logging.disable(logging.CRITICAL)
    from pypdf import PdfReader

    raw = sys.stdin.buffer.read(limits["maximum_source_bytes"] + 1)
    if not 0 < len(raw) <= limits["maximum_source_bytes"] or not raw.startswith(b"%PDF-"):
        raise ValueError("bounded original required")
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if reader.is_encrypted or not 0 < len(reader.pages) <= limits["maximum_pages"]:
        raise ValueError("physical document bounds")
    result, streams = [], 0
    for page in reader.pages:
        contents = page.get_contents()
        size = len(contents.get_data()) if contents is not None else 0
        streams += size
        if size > limits["maximum_page_stream_bytes"] or streams > limits["maximum_total_stream_bytes"]:
            raise ValueError("decoded stream bounds")
        original = column(page, limits)
        result.append(None if original is None else {
            **original, "aum_row": uti_aum_row.page_row(page, original["geometry"], limits)})
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
