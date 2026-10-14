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
from urllib.parse import urlencode, urljoin, urlsplit, unquote
import requests

from mfdataindia.enrichment.evidence import public_url

BASE = "https://mutualfund.adityabirlacapital.com"
_LANDING = "/forms-and-downloads/factsheets"
_API_MONTHLY = "/api/sitecore/CalculatorPage/GetMonthlyFactsheetsByYear"

_DS_ID_RE = re.compile(
    r'''\bfactsheetDatasourceId\s*=\s*(['"])(\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\})\1'''
)
_TITLE_RE = re.compile(r"Empower for the Month of ([A-Z][a-z]+ \d{4})")
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


def monthly_api_url(html: str, year: int) -> str:
    if type(year) is not int or not 1900 <= year <= 2100:
        raise ValueError("bounded integer factsheet year required")
    ids = {match[1].upper() for match in _DS_ID_RE.findall(html)}
    if len(ids) != 1:
        raise ValueError("one unambiguous factsheetDatasourceId required")
    return BASE + _API_MONTHLY + "?" + urlencode({"datasourceId": ids.pop(), "year": year})


def monthly_documents(data: dict, year: int) -> list[FactsheetDocument]:
    """Accept observed Sitecore success variants, never arbitrary links/periods."""
    if type(year) is not int or not 1900 <= year <= 2100:
        raise ValueError("bounded integer factsheet year required")
    if not isinstance(data, dict) or str(data.get("ReturnCode")) not in ("0", "1"):
        raise ValueError("ABSL monthly API did not report success")
    items = data.get("MonthlyFactsheets")
    if not isinstance(items, list) or len(items) > 24:
        raise ValueError("bounded monthly document list required")
    out, periods = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("invalid monthly document entry")
        link, title = item.get("DocumentLink"), item.get("DocumentTitle")
        if link in (None, "", "NA"):
            continue
        if not isinstance(link, str) or not isinstance(title, str):
            raise ValueError("document link and title required")
        if any(part in (".", "..") for part in unquote(link).split("/")):
            raise ValueError("factsheet path traversal not permitted")
        match = _TITLE_RE.fullmatch(title)
        if not match:
            raise ValueError("unrecognized factsheet period title")
        period = match[1]
        month, listed_year = period.split()
        if month not in _MONTH_ORDER or int(listed_year) != year or period in periods:
            raise ValueError("ambiguous or out-of-year factsheet period")
        url = public_url(urljoin(BASE + "/", link), {"mutualfund.adityabirlacapital.com"})
        if not url.startswith(BASE + "/-/media/bsl/files/resources/factsheets/"):
            raise ValueError("document outside official factsheet media tree")
        decoded = unquote(urlsplit(url).path)
        if (
            any(part in (".", "..") for part in decoded.split("/"))
            or any(c in decoded for c in "\\%?#")
            or not decoded.lower().endswith((".pdf", ".ashx"))
        ):
            raise ValueError("unsafe factsheet media path")
        periods.add(period)
        out.append(FactsheetDocument(title=title, url=url, period=period))
    return out


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
        return monthly_documents(self._get(monthly_api_url(html, year)).json(), year)

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
        return (int(m.group(2)), _MONTH_ORDER.get(m.group(1), 0))

    def download(self, doc: FactsheetDocument) -> bytes:
        return self._get(doc.url).content
