"""Patched dependency boundary around frozen v3 physical financial rules."""

import sys

from mfdataindia.enrichment import pdf_security_v2
from mfdataindia.enrichment._uti_book_pdf_worker_v3 import main as retained_main


def main():
    pdf_security_v2.require_runtime()
    retained_main()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
