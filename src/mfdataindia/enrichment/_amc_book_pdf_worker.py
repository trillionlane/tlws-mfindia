"""Resource-bounded no-write/no-network decoder for an original AMC book."""

import io
import json
import logging
import resource
import sys

from mfdataindia.enrichment.amc_book_pdf import policy


def main():
    limits = policy()
    resource.setrlimit(resource.RLIMIT_CPU, (limits["worker_cpu_seconds"],) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (limits["linux_worker_address_space_bytes"],) * 2)
    logging.disable(logging.CRITICAL)
    from pypdf import PdfReader

    if len(sys.argv) != 1:
        raise ValueError("no page, source URL or caller overrides permitted")
    raw = sys.stdin.buffer.read(limits["maximum_source_bytes"] + 1)
    if not 0 < len(raw) <= limits["maximum_source_bytes"] or not raw.startswith(b"%PDF-"):
        raise ValueError("source byte bound")
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if reader.is_encrypted or not 0 < len(reader.pages) <= limits["maximum_pages"]:
        raise ValueError("document page/encryption bounds")
    pages, characters, streams = [], 0, 0
    for page in reader.pages:
        contents = page.get_contents()
        size = len(contents.get_data()) if contents is not None else 0
        streams += size
        if (
            size > limits["maximum_page_stream_bytes"]
            or streams > limits["maximum_total_stream_bytes"]
        ):
            raise ValueError("decoded stream bound")
        text = page.extract_text() or ""
        characters += len(text)
        if (
            len(text) > limits["maximum_page_characters"]
            or characters > limits["maximum_total_characters"]
        ):
            raise ValueError("decoded text bound")
        pages.append(text)
    sys.stdout.write(
        json.dumps({"pages": pages, "document_pages": len(reader.pages)}, ensure_ascii=False)
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
