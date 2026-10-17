"""Two latest distinct official editions, without widening frozen pilot discovery."""

import calendar
import json
from datetime import date

from mfdataindia.enrichment import refresh_acquisition
from mfdataindia.ingest.amc_factsheets.download import (
    BASE,
    _LANDING,
    _MONTH_ORDER,
    monthly_api_url,
    monthly_documents,
)


def discover_absl(now, *, transport=refresh_acquisition.fetch):
    landing = transport(BASE + _LANDING)
    html = landing["content"].decode("utf-8")
    catalogs, editions = [landing], {}
    for year in (now.year, now.year - 1):
        catalog = transport(monthly_api_url(html, year), catalog_api=True)
        catalogs.append(catalog)
        for item in monthly_documents(json.loads(catalog["content"]), year):
            month = _MONTH_ORDER[item.period.split()[0]]
            if (year, month) > (now.year, now.month):
                raise ValueError("future publication month is not usable")
            if (year, month) in editions:
                raise ValueError("ambiguous official monthly edition")
            y, m = (year, month - 1) if month > 1 else (year - 1, 12)
            editions[year, month] = {
                "url": item.url,
                "period": item.period,
                "as_of": date(y, m, calendar.monthrange(y, m)[1]).isoformat(),
            }
        if len(editions) >= 2:
            selected = [editions[k] for k in sorted(editions, reverse=True)[:2]]
            if len({item["url"] for item in selected}) != 2:
                raise ValueError("distinct original editions required")
            return catalogs, selected
    raise ValueError("two official monthly editions required")
