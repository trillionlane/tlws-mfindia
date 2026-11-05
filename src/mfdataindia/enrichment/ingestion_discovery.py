"""Shared source discovery values; no network, database or publication on import.

Issuer catalogs are observations, not a permission to invent a URL, reporting
date or adapter. Actual PDF date validation is a separate mandatory step.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
import calendar
import hashlib
import json
import re

from mfdataindia.enrichment import uti_acquisition, kotak_acquisition
from mfdataindia.review.policy import fingerprint

SOURCES = ('absl-large-cap', 'hdfc-flexi-cap', 'absl-expanded',
           'sbi-expanded', 'kotak-expanded', 'uti-expanded')
SHA = re.compile('[0-9a-f]{64}')


class State(StrEnum):
    WAITING = 'WAITING_FOR_PUBLICATION'
    INCOMPLETE = 'INCOMPLETE_SOURCE_FILES'
    READY = 'FILES_FOUND_DATE_UNVERIFIED'
    CLAIMED = 'WORK_CLAIMED'
    UNCHANGED = 'UNCHANGED'
    FETCH_FAILED = 'FETCH_FAILED'
    PARSE_FAILED = 'PARSE_OR_VALIDATION_FAILED'
    PUBLICATION_FAILED = 'PUBLICATION_ROLLED_BACK'
    UNKNOWN = 'PUBLICATION_OUTCOME_UNKNOWN'
    PUBLISHED = 'PUBLISHED'
    HELD = 'HELD_FOR_RECONCILIATION'
    NOT_DUE = 'NOT_DUE'
    UNSUPPORTED = 'ADAPTER_NOT_AVAILABLE'


@dataclass(frozen=True)
class Discovery:
    source_key: str
    state: State
    files: tuple = ()
    reason: str = ''
    latest_period_found: str | None = None


def strict_json(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= 1048576:
        raise ValueError('SOURCE_CATALOGUE_SIZE_INVALID')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('SOURCE_CATALOGUE_DUPLICATE_MEMBER')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('SOURCE_CATALOGUE_NONFINITE')))


def uti_catalogue(raw, url):
    """Empty/partial is waiting, not a parser crash or proof of global absence."""
    value = strict_json(raw)
    if type(value) is not dict or type(value.get('rows')) is not list or len(value['rows']) > 64:
        raise ValueError('UTI_CATALOGUE_SHAPE_INVALID')
    match = uti_acquisition.CATALOGUE_PATTERN.fullmatch(url)
    if not match:
        raise ValueError('UTI_CATALOGUE_URL_INVALID')
    rows = value['rows']
    if not rows:
        return Discovery('uti-expanded', State.WAITING, reason='CHECKED_ISSUER_CATALOGUE_EMPTY')
    files = []
    for kind in ('Active', 'Passive'):
        expected = f'UTI Fund Watch ({kind})-{match[2]} {match[1]}'
        matches = [r for r in rows if type(r) is dict and r.get('name') == expected]
        if len(matches) > 1:
            raise ValueError('UTI_ENGLISH_FILE_AMBIGUOUS')
        if not matches:
            continue
        row = matches[0]
        if (row.get('doc') != row.get('url') or row.get('type') != 'pdf'
                or row.get('category') != 'Fact Sheet' or row.get('month') != match[2]
                or row.get('year') != match[1]):
            raise ValueError('UTI_ENGLISH_FILE_METADATA_INVALID')
        source_url = uti_acquisition.requested_url(row['url'], kind=kind,
                                      month=match[2], year=match[1])
        files.append((kind.lower(), source_url))
    if len(files) != 2:
        return Discovery('uti-expanded', State.INCOMPLETE, tuple(files),
                         'COMPLETE_ISSUER_LINKED_ENGLISH_PAIR_REQUIRED')
    # Reuse the legacy production parser, preserving exact URL/name metadata.
    confirmed = uti_acquisition.catalogue_books(raw, url)
    if tuple((f['kind'], f['source_url']) for f in confirmed) != tuple(files):
        raise ValueError('UTI_CATALOGUE_COMPOSITION_MISMATCH')
    return Discovery('uti-expanded', State.READY, tuple(files), 'PDF_DATES_UNVERIFIED')


def kotak_catalogue(raw, url):
    value = strict_json(raw)
    if type(value) is not dict or value.get('status') != 'Success' or str(value.get('statusCode')) != '200':
        raise ValueError('KOTAK_CATALOGUE_RESPONSE_INVALID')
    rows = value.get('subHeaderList')
    if type(rows) is not list:
        raise ValueError('KOTAK_CATALOGUE_SHAPE_INVALID')
    if not rows:
        return Discovery('kotak-expanded', State.WAITING, reason='CHECKED_ISSUER_CATALOGUE_EMPTY')
    return Discovery('kotak-expanded', State.READY,
                     (('book', kotak_acquisition.catalogue_book(raw, url)),), 'PDF_DATE_UNVERIFIED')


def absl_catalogues(source, observed, transport, *, periods):
    """Reuse the official adapter's parsers, retain typed empty/partial results."""
    from mfdataindia.ingest.amc_factsheets.download import (
        BASE, _LANDING, _MONTH_ORDER, monthly_api_url, monthly_documents)
    if source not in ('absl-large-cap','absl-expanded') or periods not in (1,2):
        raise ValueError('ABSL_DISCOVERY_SCOPE_INVALID')
    landing = transport(BASE+_LANDING)
    html = landing['content'].decode('utf-8',errors='strict')
    editions = {}
    for year in (observed.year,observed.year-1):
        raw = transport(monthly_api_url(html,year),catalog_api=True)
        for document in monthly_documents(strict_json(raw['content']),year):
            month = _MONTH_ORDER[document.period.split()[0]]
            edition = year,month
            if edition > (observed.year,observed.month) or edition in editions:
                raise ValueError('ABSL_EDITION_FUTURE_OR_AMBIGUOUS')
            editions[edition] = document.url
        if len(editions) >= periods:
            selected = [editions[k] for k in sorted(editions,reverse=True)[:periods]]
            if len(set(selected)) != periods:
                raise ValueError('ABSL_EDITION_URL_AMBIGUOUS')
            return Discovery(source,State.READY,tuple((f'book{n}',url) for n,url in enumerate(selected)),
                             'PDF_DATE_UNVERIFIED')
    return Discovery(source,State.INCOMPLETE if editions else State.WAITING,
                     reason='PARTIAL_SOURCE_FILES' if editions else 'CHECKED_ISSUER_CATALOGUE_EMPTY')


def sbi_catalogue(raw, observed):
    from mfdataindia.enrichment import sbi_book
    if type(raw) is not bytes or not 0 < len(raw) <= 1048576:
        raise ValueError('SBI_CATALOGUE_SIZE_INVALID')
    links=sbi_book.Links()
    links.feed(raw.decode('utf-8',errors='strict'))
    eligible = [url for url in links.links if sbi_book.BOOK.fullmatch(url)]
    if not eligible:
        return Discovery('sbi-expanded',State.WAITING,reason='CHECKED_ISSUER_CATALOGUE_EMPTY')
    try:
        editions = sbi_book.editions(raw,observed)
    except ValueError as error:
        # Only the existing parser's one exact missing-pair cause is a
        # completeness observation. Future/ambiguous inputs remain failures.
        if error.args != ('two latest complete SBI active/passive periods required',):
            raise
        return Discovery('sbi-expanded',State.INCOMPLETE,reason='PARTIAL_SOURCE_FILES')
    return Discovery('sbi-expanded',State.READY,tuple((f'book{n}_{kind}',url)
        for n,e in enumerate(editions) for kind,url in sorted(e['documents'].items())),
        'PDF_DATE_UNVERIFIED')


def manifest(documents):
    """Only complete bytes establish identity; URLs/ETags are not content keys."""
    if type(documents) is not list or not 1 <= len(documents) <= 1024:
        raise ValueError('DISCOVERY_DOCUMENT_SET_INVALID')
    rows = []
    for document in documents:
        if (type(document) is not dict or set(document) != {'role', 'content'}
                or type(document['role']) is not str
                or re.fullmatch('[a-z][a-z0-9_]{0,47}', document['role']) is None
                or type(document['content']) is not bytes or not 0 < len(document['content']) <= 32*1024*1024):
            raise ValueError('DISCOVERY_DOCUMENT_INVALID')
        rows.append({'role': document['role'], 'sha256': hashlib.sha256(document['content']).hexdigest()})
    if sum(len(d['content']) for d in documents) > 80*1024*1024:
        raise ValueError('DISCOVERY_AGGREGATE_BYTES_INVALID')
    if len({r['role'] for r in rows}) != len(rows):
        raise ValueError('DISCOVERY_DOCUMENT_ROLE_DUPLICATED')
    return sorted(rows, key=lambda r: r['role'])


def work(source, reporting_period, documents, derivation_version, population_hash):
    if (source not in SOURCES or type(derivation_version) is not str
            or type(population_hash) is not str or not SHA.fullmatch(derivation_version)
            or not SHA.fullmatch(population_hash) or type(reporting_period) is not str):
        raise ValueError('INGESTION_WORK_SCOPE_INVALID')
    day = date.fromisoformat(reporting_period)
    if day.isoformat() != reporting_period or day.day != calendar.monthrange(day.year, day.month)[1]:
        raise ValueError('ACTUAL_MONTH_END_REQUIRED')
    if (type(documents) is not list or not 1 <= len(documents) <= 1024
            or any(type(r) is not dict or set(r) != {'role', 'sha256'}
                   or type(r['role']) is not str or re.fullmatch('[a-z][a-z0-9_]{0,47}',r['role']) is None
                   or type(r['sha256']) is not str or not SHA.fullmatch(r['sha256']) for r in documents)
            or documents != sorted(documents, key=lambda r: r['role'])
            or len({r['role'] for r in documents}) != len(documents)):
        raise ValueError('INGESTION_WORK_DOCUMENTS_INVALID')
    body = {'format':'amc-ingestion-work-v1', 'source_key':source,
            'reporting_period':reporting_period, 'documents':documents,
            'derivation_version':derivation_version, 'population_hash':population_hash}
    return {**body, 'work_id':fingerprint(body)}


def due(source, now, previous_poll=None, *, waiting=True):
    if source not in SOURCES or not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError('DISCOVERY_CADENCE_SCOPE_INVALID')
    if previous_poll is None:
        return True
    prior = datetime.fromisoformat(previous_poll)
    if prior.utcoffset() is None or prior > now:
        raise ValueError('DISCOVERY_CADENCE_TIME_INVALID')
    # More frequent checks during ordinary month-opening discovery; this is not
    # an issuer deadline, freshness claim, or a new scheduler by itself.
    interval = 6*3600 if waiting and now.astimezone(timezone.utc).day <= 10 else 24*3600
    return (now-prior).total_seconds() >= interval
