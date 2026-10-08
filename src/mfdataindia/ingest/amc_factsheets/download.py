"""Download ABSL's consolidated monthly factsheet PDFs.

ABSL publishes ONE factsheet per AMC per month (the "Empower" PDF, ~300
pages, all schemes). Discovery works through their Sitecore site:

    1. GET the factsheets landing page and read the page-scoped JS global
       ``var factsheetDatasourceId = '{...}'`` (it is a Sitecore data
       source ID, stable but site-configured — discovered per run, never
       hardcoded).
    2. GET ``/api/sitecore/CalculatorPage/GetMonthlyFactsheetsByYear?
       datasourceId=...&year=YYYY`` -> JSON list of that year's documents
       (``DocumentTitle`` "Empower for the Month of September 2026",
       ``DocumentLink`` relative PDF path).
    3. GET the PDF (``Referer`` header required).

The newest month is selected from the titles; the document month
("September 2026") is carried on the result because the loader needs it
for provenance, and it differs from the factsheet's NAV as-of date
(August 31, 2026 in this example).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import requests

BASE = "https://mutualfund.adityabirlacapital.com"
_LANDING = "/forms-and-downloads/factsheets"
_API_MONTHLY = "/api/sitecore/CalculatorPage/GetMonthlyFactsheetsByYear"

_DS_ID_RE = re.compile(r"factsheetDatasourceId\s*=\s*'\{[0-9A-Fa-f-]+\}'")
_TITLE_RE = re.compile(r"for the Month of ([A-Z][a-z]+ \d{4})")
_MONTH_ORDER = {
    m: i + 1 for i, m in enumerate(
        ("January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"))
}


@dataclass(slots=True)
class FactsheetDocument:
    title: str
    url: str
    period: str            # "September 2026" — parsed from the title


class AbSLFactsheetClient:
    def __init__(self, *, min_delay: float = 1.0, timeout: float = 30.0,
                 user_agent: str = "MFDataIndia/0.1"):
        self.min_delay = min_delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Referer": BASE + _LANDING,
        })
        self._last = 0.0

    def _get(self, url: str) -> requests.Response:
        import time
        gap = self.min_delay - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        resp = self.session.get(url, timeout=self.timeout)
        self._last = time.monotonic()
        resp.raise_for_status()
        return resp

    def list_by_year(self, year: int) -> list[FactsheetDocument]:
        html = self._get(BASE + _LANDING).text
        m = _DS_ID_RE.search(html)
        if not m:
            raise RuntimeError(
                "factsheetDatasourceId not found on the ABSL landing page — "
                "site layout changed; update the discovery regex")
        ds_id = m.group(0).split("=", 1)[1].strip().strip("'")
        import urllib.parse
        api = (BASE + _API_MONTHLY + "?"
               + urllib.parse.urlencode({"datasourceId": ds_id, "year": year}))
        data = self._get(api).json()
        if str(data.get("ReturnCode")) != "0":
            raise RuntimeError(f"ABSL API error: {data.get('ReturnMsg')}")
        out: list[FactsheetDocument] = []
        for item in data.get("MonthlyFactsheets") or []:
            link = item.get("DocumentLink") or ""
            title = item.get("DocumentTitle") or ""
            if not link or link == "NA" or not title:
                continue
            tm = _TITLE_RE.search(title)
            period = tm.group(1) if tm else title
            out.append(FactsheetDocument(
                title=title, url=BASE + link, period=period))
        return out

    def latest(self, year: int) -> FactsheetDocument:
        docs = self.list_by_year(year)
        if not docs:
            raise RuntimeError(f"no ABSL factsheets listed for {year}")
        return max(docs, key=lambda d: self._period_key(d.period))

    @staticmethod
    def _period_key(period: str) -> tuple:
        m = re.match(r"([A-Z][a-z]+) (\d{4})", period)
        if not m:
            return (0, 0)
        return (_MONTH_ORDER.get(m.group(1), 0), int(m.group(2)))

    def download(self, doc: FactsheetDocument) -> bytes:
        return self._get(doc.url).content