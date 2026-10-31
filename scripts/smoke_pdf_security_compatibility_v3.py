"""Provider-free packaged worker/command guard smoke; not source acceptance."""

import io
import json
from pathlib import Path
import subprocess
import sys

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject

from mfdataindia.enrichment import pdf_security_v3, uti_book_pdf_v6
from mfdataindia.review.policy import fingerprint


def fixture(*, missing):
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    if not missing:
        stream = DecodedStreamObject()
        stream.set_data(b"")
        page.replace_contents(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def main():
    pdf_security_v3.require_runtime()
    empty = uti_book_pdf_v6.extract(fixture(missing=False))
    assert empty["pages"][0]["text"] == ""
    assert empty["pages"][0]["physical_left_text"] is None
    pdf_security_v3.admit_extraction(
        empty, dependency=pdf_security_v3.dependency_contract(),
        engine_hash=uti_book_pdf_v6.engine_hash(), policy_hash=fingerprint(uti_book_pdf_v6.policy()))
    try:
        uti_book_pdf_v6.extract(fixture(missing=True))
    except ValueError as error:
        assert "layout book decoder failed" in str(error)
    else:
        raise AssertionError("missing Contents was silently accepted")
    scripts = Path(__file__).resolve().parent
    for command in ("refresh_official_facts_dev.py", "refresh_registered_facts_dev.py",
                    "refresh_amc_books_dev.py"):
        result = subprocess.run([sys.executable, str(scripts / command)],
                                capture_output=True, timeout=10, check=False,
                                env={"PATH": str(Path(sys.executable).parent)})
        assert result.returncode == 1
        body = json.loads(result.stdout)
        assert body["error_type"] == "ValueError" and body["source_rows_changed"] == 0
    print(json.dumps({"packaged_pdf_security_smoke": "PASS", "source_authority": "DORMANT",
                      "provider_requests": 0, "database_writes": 0, "live_acceptance": False}))


if __name__ == "__main__":
    main()
