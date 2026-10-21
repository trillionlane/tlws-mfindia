"""Isolated resource-bounded no-network/no-write layout decoder."""

import io
import json
import logging
import resource
import sys

from mfdataindia.enrichment.layout_book_pdf_v2 import policy
from mfdataindia.enrichment import pdf_security


def main():
    pdf_security.require_runtime()
    if len(sys.argv) != 2:
        raise ValueError("one reviewed source profile required")
    limits = policy(sys.argv[1])
    resource.setrlimit(resource.RLIMIT_CPU, (limits["worker_cpu_seconds"],) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (limits["linux_worker_address_space_bytes"],) * 2)
    logging.disable(logging.CRITICAL)
    from pypdf import PdfReader

    raw = sys.stdin.buffer.read(limits["maximum_source_bytes"] + 1)
    if not 0 < len(raw) <= limits["maximum_source_bytes"] or not raw.startswith(b"%PDF-"):
        raise ValueError("layout source byte bound")
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if reader.is_encrypted or not 0 < len(reader.pages) <= limits["maximum_pages"]:
        raise ValueError("layout document encryption/page bounds")
    pages, characters, streams = [], 0, 0
    for page in reader.pages:
        contents = page.get_contents()
        if contents is None:
            raise ValueError("missing Contents is not an explicit empty stream")
        size = len(contents.get_data())
        streams += size
        if (
            size > limits["maximum_page_stream_bytes"]
            or streams > limits["maximum_total_stream_bytes"]
        ):
            raise ValueError("layout decoded stream bounds")
        text = page.extract_text(extraction_mode="layout", layout_mode_strip_rotated=True) or ""
        characters += len(text)
        if (
            len(text) > limits["maximum_page_characters"]
            or characters > limits["maximum_total_characters"]
        ):
            raise ValueError("layout text bounds")
        pages.append(text)
    sys.stdout.write(json.dumps({"pages": pages, "document_pages": len(pages)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
