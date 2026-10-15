"""Isolated larger-factsheet decoder; no change to the existing PDF v2 policy."""
import io
import json
import logging
import resource
import sys


def main():
    from mfdataindia.enrichment.idcw_discovery import policy
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
        raise ValueError("bounded PDF required")
    numbers = [int(x) for x in sys.argv[1:]]
    if not 1 <= len(numbers) <= 2 or numbers != sorted(set(numbers)):
        raise ValueError("explicit selected pages required")
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or not 0 < len(reader.pages) <= 400 or not all(1 <= p <= len(reader.pages) for p in numbers):
        raise ValueError("unsupported document/pages")
    result = []
    for number in numbers:
        page = reader.pages[number - 1]
        stream = page.get_contents()
        if stream and len(stream.get_data()) > limits["maximum_page_stream_bytes"]:
            raise ValueError("page stream bound")
        text = page.extract_text() or ""
        if not 0 < len(text) <= limits["maximum_page_characters"]:
            raise ValueError("page text bound")
        result.append({"page": number, "text": text})
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never echo source payloads or environment values on decode failure.
        sys.exit(1)
