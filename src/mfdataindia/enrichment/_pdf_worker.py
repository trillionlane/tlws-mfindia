"""Resource-limited subprocess entry point. No network, DB or persistent writes."""

import io
import json
import logging
import resource
import sys

from mfdataindia.enrichment.pdf_evidence import policy


def main():
    limits = policy()
    resource.setrlimit(resource.RLIMIT_CPU, (limits["worker_cpu_seconds"],) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (limits["linux_worker_address_space_bytes"],) * 2)
    logging.disable(logging.CRITICAL)
    from pypdf import PdfReader

    data = sys.stdin.buffer.read(limits["maximum_source_bytes"] + 1)
    if not 0 < len(data) <= limits["maximum_source_bytes"] or not data.startswith(b"%PDF-"):
        raise ValueError("invalid source")
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or not 0 < len(reader.pages) <= limits["maximum_pages"]:
        raise ValueError("unsupported document")
    texts, total_chars, total_streams = [], 0, 0
    for page in reader.pages:
        contents = page.get_contents()
        size = len(contents.get_data()) if contents is not None else 0
        total_streams += size
        if size > limits["maximum_page_stream_bytes"] or total_streams > limits["maximum_total_stream_bytes"]:
            raise ValueError("stream bounds exceeded")
        text = page.extract_text() or ""
        total_chars += len(text)
        if len(text) > limits["maximum_page_characters"] or total_chars > limits["maximum_total_characters"]:
            raise ValueError("text bounds exceeded")
        texts.append(text)
    sys.stdout.write(json.dumps(texts, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Do not dump untrusted document content or sensitive environment values.
        sys.exit(1)
