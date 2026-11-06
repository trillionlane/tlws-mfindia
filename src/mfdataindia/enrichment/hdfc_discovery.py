"""Issuer-linked dynamic discovery, separate from the historical pinned parser.

An edition name is a discovery hint, never its actual reporting date. A new
unreviewed financial layout is held for adapter review rather than forced
through the old exact-edition publisher. HTTP denial remains FETCH_FAILED.
"""
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, unquote, urlsplit

from mfdataindia.enrichment import acquisition
from mfdataindia.enrichment.ingestion_discovery import Discovery, State

LISTING = 'https://www.hdfcfund.com/mutual-funds/factsheets'
MONTHS = {m.lower(): n for n,m in enumerate(('January','February','March','April','May','June',
          'July','August','September','October','November','December'),1)}
BOOK = re.compile(r'/s3fs-public/20[0-9]{2}-[01][0-9]/HDFC MF Factsheet - ([A-Za-z]+) (20[0-9]{2})\.pdf')


def eligible(url):
    parsed = urlsplit(url)
    match = BOOK.fullmatch(unquote(parsed.path))
    return (parsed.scheme == 'https' and parsed.netloc == 'files.hdfcfund.com'
            and not parsed.query and not parsed.fragment and not parsed.username
            and match is not None and match[1].lower() in MONTHS)


def fetch(url):
    if url != LISTING and not eligible(url):
        raise ValueError('HDFC_DISCOVERY_URL_NOT_ELIGIBLE')
    return acquisition._fetch(url, {'www.hdfcfund.com'} if url == LISTING else {'files.hdfcfund.com'},
        timeout=30, maximum=1048576 if url == LISTING else 8*1048576,
        media_types=('text/html',) if url == LISTING else ('application/pdf',), allow_redirects=False)


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get('href')
        if tag == 'a' and href:
            url = urljoin(LISTING, href)
            if eligible(url):
                self.urls.add(url)


def discover(raw, observed):
    if type(raw) is not bytes or not 0 < len(raw) <= 1048576:
        raise ValueError('HDFC_DISCOVERY_RESPONSE_INVALID')
    parser = Links()
    parser.feed(raw.decode('utf-8', errors='strict'))
    if not parser.urls:
        return Discovery('hdfc-flexi-cap', State.INCOMPLETE,
                         reason='ISSUER_LISTING_NO_ELIGIBLE_BOOK_LINK')
    by_edition = {}
    for url in sorted(parser.urls):
        match = BOOK.fullmatch(unquote(urlsplit(url).path))
        edition = int(match[2]), MONTHS[match[1].lower()]
        if edition > (observed.year, observed.month):
            raise ValueError('HDFC_FUTURE_EDITION_LINK')
        by_edition.setdefault(edition, set()).add(url)
    latest = by_edition[max(by_edition)]
    if len(latest) != 1:
        raise ValueError('HDFC_EDITION_LINK_AMBIGUOUS')
    return Discovery('hdfc-flexi-cap', State.READY, (('book', next(iter(latest))),),
                     reason='PDF_DATE_UNVERIFIED')
