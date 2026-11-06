from datetime import datetime,timezone
import pytest
from mfdataindia.enrichment import hdfc_discovery as h

NOW=datetime(2026,11,5,tzinfo=timezone.utc)
SEPTEMBER='https://files.hdfcfund.com/s3fs-public/2026-10/HDFC%20MF%20Factsheet%20-%20September%202026.pdf'
OCTOBER=SEPTEMBER.replace('2026-10','2026-11').replace('September','October')


def test_dynamic_issuer_link_selection_does_not_infer_pdf_reporting_date_or_guess_url():
    result=h.discover(f'<a href="{SEPTEMBER}">older</a><a href="{OCTOBER}">current</a>'.encode(),NOW)
    assert result.files==( ('book',OCTOBER), )
    assert result.latest_period_found is None and result.reason=='PDF_DATE_UNVERIFIED'
    assert h.discover(b'<html>No eligible links in checked response</html>',NOW).state.value=='INCOMPLETE_SOURCE_FILES'


@pytest.mark.parametrize('url',[OCTOBER.replace('files.hdfcfund.com','attacker.test'),
    OCTOBER+'?redirect=other',OCTOBER+'#fragment','http://files.hdfcfund.com/test.pdf',
    'https://files.hdfcfund.com/private.csv'])
def test_unregistered_hosts_credentials_queries_fragments_and_nonbook_paths_are_not_eligible(url):
    assert not h.eligible(url)


def test_two_distinct_urls_for_one_latest_edition_are_ambiguous():
    other=OCTOBER.replace('/2026-11/','/2026-10/')
    with pytest.raises(ValueError,match='AMBIGUOUS'):
        h.discover(f'<a href="{OCTOBER}">one</a><a href="{other}">two</a>'.encode(),NOW)
