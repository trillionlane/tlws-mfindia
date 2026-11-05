"""Actual retained issuer panels; no live source fetch and no synthetic values."""
import json
import pytest
from copy import deepcopy
from pathlib import Path
from mfdataindia.enrichment import kotak_book_v2 as kotak_book

FIXTURE = Path(__file__).parent / 'fixtures/kotak/october-2026-panels.json'

def test_actual_october_panels_are_recognized():
    extraction = json.loads(FIXTURE.read_text())
    assert extraction['source_hash'] == '105e49dfb9787698644810d13a01065bae940f18a80d8c60f082ef71f706dda2'
    assert [d['page']['page'] for d in kotak_book.detail_pages(extraction)] == [10, 12, 14]

def details():
    return kotak_book.detail_pages(json.loads(FIXTURE.read_text()))


def test_actual_flexicap_values_and_conflicting_ber_are_not_silently_selected():
    row = kotak_book.fields(details()[0], '2026-10-31')
    assert row['aum']['value'] == '52614.58'
    assert row['fund_managers']['value'] == ['Mr. Harsha Upadhyaya']
    assert row['benchmark_name']['value'] == 'Nifty 500 TRI (Tier 1)'
    assert row['risk_level']['value'] == 'Very High'
    # Main panel says 1.44; the adjacent same-fund October change table says
    # 1.45. Neither value may be selected by this method as a verified BER.
    assert row['base_expense_ratio']['value'] is None
    assert row['base_expense_ratio']['reason'] == 'same_period_expense_values_conflict'


def test_other_actual_aum_values_are_not_aaum_or_nav():
    assert [kotak_book.fields(d, '2026-10-31')['aum']['value'] for d in details()] == ['52614.58', '29757.15', '30785.40']


def test_actual_dates_not_url_or_nav_dates_and_unchanged_original_text():
    import hashlib
    extraction = json.loads(FIXTURE.read_text())
    before = json.dumps(extraction, sort_keys=True)
    for d in kotak_book.detail_pages(extraction):
        assert hashlib.sha256(d['page']['text'].encode()).hexdigest() == d['page']['text_hash']
        assert all(v['value'] is None for v in kotak_book.fields(d, '2026-09-30').values())
    assert json.dumps(extraction, sort_keys=True) == before


def test_historical_v1_layout_and_values_are_identical():
    from test_kotak_book import extraction
    from mfdataindia.enrichment import kotak_book as old
    sample = extraction()
    assert kotak_book.detail_pages(sample) == old.detail_pages(sample)
    for detail in kotak_book.detail_pages(sample):
        assert kotak_book.fields(detail, '2026-09-30') == old.fields(detail, '2026-09-30')


@pytest.mark.parametrize('old,new,key', [
    ('Monthly AUM as on', 'Monthly AAUM as on', 'aum'),
    ('(Rs. in Cr.)', '(Units)', 'aum'),
    ('October 31, 2026 (Rs. in Cr.)', 'September 30, 2026 (Rs. in Cr.)', 'aum'),
    ('Name of Fund Manager:', 'Other person:', 'fund_managers'),
    ('Nifty 500 TRI (Tier 1)', 'Nifty 500 PRI (Tier 1)', 'benchmark_name'),
    ('The risk of the scheme is', 'The risk of the benchmark is', 'risk_level'),
])
def test_changed_actual_anchors_never_borrow_other_panel_values(old, new, key):
    d = deepcopy(details()[0])
    d['left'] = d['left'].replace(old, new)
    d['page']['text'] = d['page']['text'].replace(old, new)
    assert kotak_book.fields(d, '2026-10-31')[key]['value'] is None


def test_ambiguous_duplicate_fund_panel_cannot_select_a_value():
    from mfdataindia.enrichment import amfi_summary_identity
    from unittest.mock import patch
    d = details()[0]
    identity = {'amc': kotak_book.AMC}
    with patch.object(amfi_summary_identity, 'exact_matches', return_value=([{'fund_name': 'Kotak Flexicap Fund'}], None)):
        result = kotak_book.derive(identity, [], [d, deepcopy(d)], '2026-10-31')
    assert result['reason'] == 'monthly_heading_missing_or_ambiguous'


def test_actual_multi_cap_complete_manager_panel_retains_both_co_managers():
    assert kotak_book.fields(details()[1], '2026-10-31')['fund_managers']['value'] == ['Mr. Devender Singhal', 'Mr. Abhishek Bisen']
    d = deepcopy(details()[1])
    d['left'] = d['left'].replace('Mr. Abhishek Bisen', 'Unknown manager')
    assert kotak_book.fields(d, '2026-10-31')['fund_managers']['value'] is None


def test_new_projection_only_adds_exact_retained_specification_membership():
    root = Path(__file__).resolve().parents[1]
    old = (root/'sql/optional/amc_book_current_projection_v2.sql.inc').read_text()
    new = (root/'sql/optional/amc_book_current_projection_v3.sql.inc').read_text()
    added = """    CROSS JOIN LATERAL (
        SELECT authority.document->'source_specification_hashes'->>source.key AS specification_hash
        UNION
        SELECT jsonb_array_elements_text(COALESCE(
            authority.document->'retained_source_specification_hashes'->source.key,'[]'::jsonb))
    ) allowed
"""
    assert new.count(added) == 1
    restored = new.replace(added,'',1).replace('allowed.specification_hash', "authority.document->'source_specification_hashes'->>source.key AS specification_hash",1)
    assert restored == old  # All financial/source/identity expressions unchanged.
