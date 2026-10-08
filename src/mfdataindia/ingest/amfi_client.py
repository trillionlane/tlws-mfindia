"""HTTP client for AMFI's authoritative NAV data.

AMFI is the sole NAV authority for MFDataIndia. Two facts make that work without
an India server:

* ``www.amfiindia.com`` is geo-blocked from some networks, but
  ``portal.amfiindia.com`` serves the same paths and is reachable globally;
* AMFI publishes a bulk NAV *history* report (``DownloadNAVHistoryReport_Po.aspx``)
  alongside the same-day snapshot (``NAVAll.txt``), so both the daily refresh and
  the 5-year backfill come from one source.

Endpoints used:

* ``GET /spages/NAVAll.txt`` — same-day NAV for the whole universe (all scheme types);
* ``GET /DownloadNAVHistoryReport_Po.aspx?frmdt=..&todt=..&tp=..&mc=0`` — NAV for a
  date range. ``tp`` selects scheme type and each covers a disjoint slice, so all
  three are fetched for a complete history:

    * ``tp=1`` Open Ended
    * ``tp=2`` Close Ended
    * ``tp=3`` Interval

Only ``requests`` is used, and only for fetching; parsing stays stdlib-only.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, Optional

import requests

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://portal.amfiindia.com"
DEFAULT_USER_AGENT = "MFDataIndia/0.1 (+open data; regular plan NAV history)"

#: tp codes -> scheme type, in the order fetched.
SCHEME_TYPES: dict[int, str] = {1: "Open Ended", 2: "Close Ended", 3: "Interval"}


@dataclass(slots=True)
class FetchResult:
    """One fetched payload with provenance, matching mf.source_metadata."""

    url: str
    http_status: int
    text: str
    content_sha256: str
    content_bytes: int
    fetched_at: datetime
    duration_ms: int
    acquisition: str = "LIVE"

    def as_source_metadata(self, source: str, entity_kind: str, entity_key: str) -> dict:
        """Shape for inserting into mf.source_metadata."""
        return {
            "source": source,
            "endpoint": self.url.split("?")[0],
            "entity_kind": entity_kind,
            "entity_key": entity_key,
            "http_status": self.http_status,
            "content_hash": self.content_sha256,
            "content_bytes": self.content_bytes,
            "acquisition": self.acquisition,
            "request_url": self.url,
            "fetched_at": self.fetched_at,
        }


def _amfi_date(d: date) -> str:
    """AMFI's ``DD-Mon-YYYY`` parameter format, e.g. ``01-Jan-2024``."""
    return d.strftime("%d-%b-%Y")


class AmfiClient:
    """Fetch AMFI NAV data with retry, throttle, and provenance."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        min_delay: float = 1.0,
        max_retries: int = 4,
        timeout: float = 120.0,
        user_agent: str = DEFAULT_USER_AGENT,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.min_delay = min_delay
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_request_at = 0.0
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def _throttle(self) -> None:
        """Keep a minimum gap between requests; AMFI is a human-facing server."""
        gap = self.min_delay - (time.monotonic() - self._last_request_at)
        if gap > 0:
            time.sleep(gap)

    def _get(self, path: str, params: Optional[dict] = None) -> FetchResult:
        """GET with exponential backoff, honouring ``Retry-After``.

        429 and 5xx are retried; other non-200s fail immediately so a bad URL or a
        geo-block surfaces loudly instead of looping.
        """
        url = f"{self.base_url}{path}"
        attempt = 0
        while True:
            self._throttle()
            started = time.monotonic()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                wait = min(2 ** attempt, 60)
                log.warning("request error (%s); retry %d/%d in %ss",
                            exc, attempt, self.max_retries, wait)
                time.sleep(wait)
                continue
            duration_ms = int((time.monotonic() - started) * 1000)
            self._last_request_at = time.monotonic()

            if resp.status_code in (429, 500, 502, 503, 504):
                attempt += 1
                if attempt > self.max_retries:
                    raise requests.HTTPError(
                        f"AMFI returned {resp.status_code} after {attempt} tries",
                        response=resp)
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() \
                    else min(2 ** attempt, 60)
                log.warning("AMFI %s %d; retry %d/%d in %ss",
                            path, resp.status_code, attempt, self.max_retries, wait)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            # AMFI serves latin-1-ish bytes with an occasional UTF-8 character
            # (e.g. a right single quote); never let one bad byte kill the fetch.
            text = resp.content.decode("utf-8", errors="replace")
            return FetchResult(
                url=resp.url,
                http_status=resp.status_code,
                text=text,
                content_sha256=hashlib.sha256(resp.content).hexdigest(),
                content_bytes=len(resp.content),
                fetched_at=datetime.now(timezone.utc),
                duration_ms=duration_ms,
            )

    # -- public fetchers -----------------------------------------------------

    def fetch_navall(self) -> FetchResult:
        """Same-day NAV snapshot for the whole universe (all scheme types).

        The ``?t=<epoch>`` value is a cache-buster AMFI itself uses; any value works.
        """
        return self._get("/spages/NAVAll.txt", params={"t": int(time.time())})

    def fetch_nav_history(
        self,
        from_date: date,
        to_date: date,
        tp: int,
    ) -> FetchResult:
        """NAV history for one scheme-type slice over a date range.

        :param tp: 1 = Open Ended, 2 = Close Ended, 3 = Interval (disjoint slices).
        """
        if tp not in SCHEME_TYPES:
            raise ValueError(f"tp must be one of {sorted(SCHEME_TYPES)}, got {tp}")
        return self._get(
            "/DownloadNAVHistoryReport_Po.aspx",
            params={
                "frmdt": _amfi_date(from_date),
                "todt": _amfi_date(to_date),
                "tp": tp,
                "mc": 0,
            },
        )

    def iter_nav_history(
        self,
        from_date: date,
        to_date: date,
        *,
        chunk_days: int = 90,
        tps: tuple[int, ...] = (1, 2, 3),
    ) -> Iterator[tuple[date, date, int, FetchResult]]:
        """Yield ``(chunk_from, chunk_to, tp, result)`` across the full range.

        Chunking bounds response size and memory (a year of history is ~330 MB of
        text), and each ``(chunk, tp)`` is a natural resume checkpoint in
        ``mf.ingest_checkpoints`` so an interrupted 5-year backfill restarts
        mid-way rather than from zero.
        """
        for chunk_from, chunk_to, tp in self.iter_nav_history_ranges(
            from_date, to_date, chunk_days=chunk_days, tps=tps
        ):
            result = self.fetch_nav_history(chunk_from, chunk_to, tp)
            yield chunk_from, chunk_to, tp, result

    @staticmethod
    def iter_nav_history_ranges(
        from_date: date,
        to_date: date,
        *,
        chunk_days: int = 90,
        tps: tuple[int, ...] = (1, 2, 3),
    ) -> Iterator[tuple[date, date, int]]:
        """Yield fetch coordinates without making a network request.

        Jobs use this form so they can consult the durable checkpoint before
        fetching a completed chunk. ``iter_nav_history`` remains the convenient
        fetch-all interface for callers that do not use checkpoints.
        """
        if chunk_days < 1:
            raise ValueError("chunk_days must be at least 1")
        invalid_tps = [tp for tp in tps if tp not in SCHEME_TYPES]
        if invalid_tps:
            raise ValueError(f"tp must be one of {sorted(SCHEME_TYPES)}, got {invalid_tps[0]}")
        if from_date > to_date:
            return
        chunk_from = from_date
        while chunk_from <= to_date:
            chunk_to = min(chunk_from + timedelta(days=chunk_days - 1), to_date)
            for tp in tps:
                yield chunk_from, chunk_to, tp
            chunk_from = chunk_to + timedelta(days=1)
