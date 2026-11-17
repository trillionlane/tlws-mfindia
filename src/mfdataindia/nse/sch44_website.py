"""Capture the official production website export into a private archive."""

from argparse import ArgumentParser
from datetime import date, datetime, timezone
from hashlib import sha256
import os
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

from . import contract, sch44
from .sch44_pilot import PRODUCTION_WEB_PREFIX, PilotError, PrivateArchive, json_bytes

URL = 'https://www.nseinvest.com/nsemfdesk/schememaster.htm'
JOB = 'tlws-mf-data-nse-sch44-production-web'
IST = ZoneInfo('Asia/Kolkata')


def filename_date(filename, observed):
    match = re.fullmatch(r'NSE_NSEINVEST_ALL_(\d{2})(\d{2})(\d{4})\.txt', filename)
    if not match:
        raise PilotError('NSE_FILENAME_UNRECOGNIZED')
    day, month, year = map(int, match.groups())
    try:
        generated = date(year, month, day)
    except ValueError:
        raise PilotError('NSE_GENERATION_DATE_INVALID') from None
    if generated > observed.astimezone(IST).date():
        raise PilotError('NSE_GENERATION_DATE_IN_FUTURE')
    return generated


def _issuer_origin(url):
    parts = urlsplit(url)
    return (parts.scheme, parts.netloc) == ('https', 'www.nseinvest.com')


def _route_issuer_request(route):
    request = route.request
    if not _issuer_origin(request.url):
        route.abort()
    elif request.method == 'POST':
        # Prevent an HTTP 307/308 from forwarding the form to another host.
        response = route.fetch(max_redirects=0, timeout=30000)
        if 300 <= response.status < 400:
            route.abort()
        else:
            route.fulfill(response=response)
    else:
        route.continue_()


def download(member_id):
    """Use the issuer's own form and JavaScript in one isolated browser context."""
    if not re.fullmatch(r'\d{5,20}', member_id):
        raise PilotError('MEMBER_CONFIGURATION_UNAVAILABLE')
    from playwright.sync_api import Error, sync_playwright

    try:
        with sync_playwright() as runtime:
            with runtime.chromium.launch(chromium_sandbox=True, headless=True) as browser:
                context = browser.new_context(accept_downloads=True, service_workers='block')
                # Keep the member identifier and page-created traffic on the approved issuer origin.
                context.route('**/*', _route_issuer_request)
                page = context.new_page()
                page.set_default_timeout(15000)
                response = page.goto(URL, wait_until='domcontentloaded', timeout=30000)
                if response is None or not response.ok or not _issuer_origin(page.url):
                    raise PilotError('NSE_FORM_UNAVAILABLE')
                page.locator('#memberId').fill(member_id)
                page.locator('#cmbSchemeType').select_option('consolidatedschememaster')
                with page.expect_download(timeout=90000) as event:
                    page.locator('#btnExportText').click()
                export = event.value
                if not _issuer_origin(export.url.removeprefix('blob:')):
                    raise PilotError('NSE_DOWNLOAD_ORIGIN_DIFFERS')
                return export.suggested_filename, sch44._read(export.path(), contract.MAX_BYTES)
    except Error:
        raise PilotError('NSE_FORM_OR_DOWNLOAD_FAILED') from None


def run_capture(archive, execution, fetch, now=lambda: datetime.now(timezone.utc)):
    if not re.fullmatch(JOB + r'-[a-z0-9]{1,20}', execution):
        raise PilotError('CAPTURE_EXECUTION_INVALID')
    key = f'retrievals/{execution}/'
    attempt = {'format': 'nse-sch44-production-web-retrieval-v1',
               'environment': 'PRODUCTION_WEBSITE', 'execution': execution,
               'started_at_utc': now().isoformat(), 'publication': 'NOT_ATTEMPTED'}
    archive.create(key + 'attempt.json', json_bytes(attempt))
    receipt = {**attempt, 'result': 'FAILED', 'reason': None, 'source_scope': 'UNVERIFIED',
               'effective_business_date': None, 'retrieved_at_utc': None}
    try:
        filename, data = fetch()
        observed = now()
        receipt['retrieved_at_utc'] = observed.isoformat()
        generated = filename_date(filename, observed)
        rows = sch44.parse(data)
        digest = sha256(data).hexdigest()
        payload = archive.create(f'payloads/sha256/{digest}.txt', data, deduplicate=True)
        receipt.update(result='CAPTURED', filename=filename, generation_date=generated.isoformat(),
                       generation_date_evidence='NSE_FILENAME', byte_length=len(data),
                       sha256=digest, row_count=len(rows), layout=sch44.PROFILE,
                       payload=payload, freshness=('PRIOR_DATE' if generated < observed.astimezone(IST).date()
                                                   else 'SAME_IST_DATE'))
    except (PilotError, contract.ImportError) as error:
        receipt['reason'] = str(error)
    except (OSError, ImportError, requests.RequestException, ValueError):
        receipt['reason'] = 'CAPTURE_OR_ARCHIVE_INCOMPLETE'
    receipt['completed_at_utc'] = now().isoformat()
    archive.create(key + 'receipt.json', json_bytes(receipt))
    return receipt


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', required=True)
    args = parser.parse_args(argv)
    if os.environ.get('CLOUD_RUN_JOB') != JOB:
        parser.exit(2, 'UNAPPROVED_RUN_JOB\n')
    try:
        with requests.Session() as session:
            session.trust_env = False
            archive = PrivateArchive(session, args.bucket, prefix=PRODUCTION_WEB_PREFIX)
            receipt = run_capture(archive, os.environ.get('CLOUD_RUN_EXECUTION', ''),
                                  lambda: download(os.environ.get('NSE_SCH44_WEBSITE_MEMBER_ID', '')))
    except (PilotError, requests.RequestException, OSError, ValueError):
        parser.exit(2, 'CAPTURE_ARCHIVE_UNAVAILABLE\n')
    print(json_bytes(receipt).decode(), end='')
    return 0 if receipt['result'] == 'CAPTURED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
