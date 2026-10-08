"""Groww fund-page client and parser.

Groww serves robot-allowed ``/mutual-funds/<slug>`` pages whose data is in the
embedded ``<script id="__NEXT_DATA__">`` JSON. The payload is regular-plan-correct
(the regular expense ratio differs from the direct one), which is why Groww is the
preferred enrichment source over Scripbox's direct-only bulk list.

There is no ISIN URL and no public search endpoint, so funds are located by
*derived slug*, and every page is accepted only when its ``isin`` matches the
fund's ISIN — a wrong guess either 404s or mismatches and is flagged, never
silently loaded.

`requests` is used for fetching; parsing stays stdlib.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import requests

log = logging.getLogger(__name__)

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

#: Words AMFI puts in scheme names that Groww does not carry into the slug.
_TRAILING_OPTION_WORDS = re.compile(
    r"(?:\s+(?:growth|idcw|dividend|bonus|option|plan)\b)+$", re.I)


def extract_fund_data(html: str) -> Optional[dict]:
    """Pull ``props.pageProps.mfServerSideData`` from a Groww fund page."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        page = json.loads(m.group(1))["props"]["pageProps"]
    except (json.JSONDecodeError, KeyError):
        return None
    return page.get("mfServerSideData")


def slugify_name(name: str, *, amp_as_and: bool = True) -> str:
    """Lowercase, hyphenate, collapse. Groww slugs use hyphens for all separators.

    ``amp_as_and`` toggles the "&" -> "and" expansion: Groww is inconsistent
    ("Large & Mid Cap" appears as both ``large-and-mid-cap`` and ``large-mid-cap``),
    so both variants are tried.
    """
    s = name.strip().lower()
    s = _TRAILING_OPTION_WORDS.sub("", s)
    if amp_as_and:
        s = s.replace("&", " and ")
    s = s.replace("&", " ").replace("'", "").replace('"', "")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return re.sub(r"-{2,}", "-", s)


def _option_word(option_type: str) -> str:
    return {"GROWTH": "growth", "IDCW": "idcw", "DIVIDEND": "dividend",
            "BONUS": "bonus"}.get(option_type, "growth")


def slug_candidates(scheme_name: str, plan_type: str, option_type: str) -> list[str]:
    """All plausible Groww slugs for a fund, most likely first.

    Groww's suffix shape and "&" handling are inconsistent, so several candidates
    are generated. The fetch validates each by ISIN, so wrong guesses are harmless.
    """
    plan_word = "regular" if plan_type == "REGULAR" else "direct"
    opt = _option_word(option_type)
    candidates: list[str] = []
    for amp_as_and in (True, False):
        base = slugify_name(scheme_name, amp_as_and=amp_as_and)
        for suffix in (f"{plan_word}-{opt}", f"{plan_word}-plan-{opt}", opt):
            c = f"{base}-{suffix}"
            if c not in candidates:
                candidates.append(c)
    return candidates


def derive_slug(scheme_name: str, plan_type: str, option_type: str) -> str:
    """The single most likely Groww slug (first candidate)."""
    return slug_candidates(scheme_name, plan_type, option_type)[0]


class GrowwClient:
    """Fetch Groww fund pages with throttle/retry."""

    BASE = "https://groww.in"

    def __init__(self, *, min_delay: float = 1.0, timeout: float = 20.0,
                 max_retries: int = 3, user_agent: str = "MFDataIndia/0.1"):
        self.min_delay = min_delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._last = 0.0

    def fetch_fund(self, slug: str) -> tuple[int, Optional[dict]]:
        """Return ``(http_status, mfServerSideData)``; data is None on 404/parse fail."""
        import time
        url = f"{self.BASE}/mutual-funds/{slug}"
        attempt = 0
        while True:
            gap = self.min_delay - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap)
            try:
                resp = self.session.get(url, timeout=self.timeout)
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
                    raise requests.HTTPError(f"groww {resp.status_code}", response=resp)
                time.sleep(min(2 ** attempt, 30))
                continue
            resp.raise_for_status()
            return resp.status_code, extract_fund_data(resp.text)

    def fetch_by_isin(
        self, scheme_name: str, plan_type: str, option_type: str, want_isin: str
    ) -> tuple[Optional[str], Optional[dict]]:
        """Try slug candidates until one's page ISIN matches ``want_isin``.

        Returns ``(slug, mfServerSideData)`` on a match, else ``(None, None)``.
        The ISIN check is what makes slug guessing safe: a 404 or an ISIN mismatch
        is a clean miss, never wrong data.
        """
        for slug in slug_candidates(scheme_name, plan_type, option_type):
            status, data = self.fetch_fund(slug)
            if data is None:
                continue
            if data.get("isin") == want_isin:
                return slug, data
        return None, None
