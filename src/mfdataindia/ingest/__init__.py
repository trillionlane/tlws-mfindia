"""Source adapters for MFDataIndia ingestion.

AMFI is the sole NAV authority:

* :mod:`mfdataindia.ingest.amfi_client` — fetches from ``portal.amfiindia.com``
  (globally reachable; ``www.amfiindia.com`` is geo-blocked);
* :mod:`mfdataindia.ingest.amfi_navall` — parses the daily ``NAVAll.txt`` snapshot
  (both the legacy 6-column and current 8-column layouts);
* :mod:`mfdataindia.ingest.amfi_nav_history` — parses the bulk NAV *history*
  report used to backfill the required 5-year window.

``api.mfapi.in`` is intentionally **not** a NAV source (unreliable).
"""

from mfdataindia.ingest.amfi_client import AmfiClient, FetchResult

__all__ = ["AmfiClient", "FetchResult"]
