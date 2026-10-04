"""Scripbox client: AMC listing, per-AMC fund listing, and fund detail pages.

Scripbox is the reliable enrichment source — it returns *real* regular-plan
slugs and ISINs (no slug guessing). All fetches parse the robot-allowed HTML
``__NEXT_DATA__`` (the ``*/data/`` JSON route is robots-disallowed, so we use HTML).

Layout:
  GET /mutual-fund/amc                            -> pageProps.serverResponse = [amc...]
  GET /mutual-fund/amc/{amc_slug}/isin-fair-market-values
                                                   -> pageProps.serverResponse.items =
                                                      [factsheet...] (direct funds, each with
                                                      fund_variant[] of all variants + isin)
  GET /mutual-fund/{fund_slug}                    -> pageProps.factsheetData (102 fields)

Pagination on the fund list uses ``?items_per_page=1000`` so one request covers a
whole AMC.

`requests` is used for fetching; parsing stays stdlib.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Iterator, Optional

import requests

log = logging.getLogger(__name__)

_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _next_data(html: str) -> Optional[dict]:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))["props"]["pageProps"]
    except (json.JSONDecodeError, KeyError):
        return None


class ScripboxClient:
    BASE = "https://scripbox.com"

    def __init__(self, *, min_delay: float = 1.5, timeout: float = 30.0,
                 max_retries: int = 3, user_agent: str = "MFDataIndia/0.1"):
        self.min_delay = min_delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._last = 0.0

    def _get_page_props(self, path: str, params: Optional[dict] = None) -> tuple[int, Optional[dict]]:
        attempt = 0
        while True:
            gap = self.min_delay - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap)
            try:
                resp = self.session.get(f"{self.BASE}{path}", params=params, timeout=self.timeout)
            except requests.RequestException:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                time.sleep(min(2 ** attempt, 30))
                continue
            self._last = time.monotonic()
            if resp.status_code == 404:
                return 404, None
            if resp.status_code in (429, 500, 502, 503, 504):
                attempt += 1
                if attempt > self.max_retries:
                    raise requests.HTTPError(f"scripbox {resp.status_code}", response=resp)
                time.sleep(min(2 ** attempt, 30))
                continue
            resp.raise_for_status()
            return resp.status_code, _next_data(resp.text)

    def fetch_amc_list(self) -> list[dict[str, Any]]:
        """All AMCs with ``amc_slug``."""
        status, pp = self._get_page_props("/mutual-fund/amc")
        if not pp:
            return []
        sr = pp.get("serverResponse") or []
        return sr if isinstance(sr, list) else []

    def fetch_amc_funds(self, amc_slug: str) -> list[dict[str, Any]]:
        """All funds of one AMC as full factsheet dicts (direct funds, each with
        fund_variant[]). Uses a large page size so one request covers the AMC."""
        path = f"/mutual-fund/amc/{amc_slug}/isin-fair-market-values"
        status, pp = self._get_page_props(path, params={"items_per_page": 1000, "page_index": 1})
        if not pp:
            return []
        sr = pp.get("serverResponse") or {}
        items = sr.get("items") if isinstance(sr, dict) else None
        return items or []

    def fetch_fund(self, fund_slug: str) -> Optional[dict]:
        """Full ``factsheetData`` for one fund page."""
        status, pp = self._get_page_props(f"/mutual-fund/{fund_slug}")
        if not pp:
            return None
        fs = pp.get("factsheetData")
        return fs if isinstance(fs, dict) else None


def regular_variants(amc_funds: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Yield regular-plan variant stubs (slug + isin) from a fund list.

    Each direct fund carries ``fund_variant[]`` listing every plan/option variant
    with its own ``fund_slug`` and ``isin_code``. Regular variants are those with
    ``is_direct`` falsy.
    """
    for fund in amc_funds:
        for v in fund.get("fund_variant") or []:
            if not v.get("is_direct") and v.get("fund_slug"):
                yield {
                    "fund_slug": v["fund_slug"],
                    "isin_code": v.get("isin_code"),
                    "amfi_code": v.get("amfi_code"),
                }
