"""Only an explicitly reviewed dependency-label transition may reuse old facts."""
from copy import deepcopy
import pytest

from mfdataindia.enrichment import monthly_derivation_replay as replay
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.store import Conflict
from test_monthly_facts import parsed
from test_official_facts import identity


def snapshot(version):
    body = {'format':'official-monthly-fund-facts-v1', 'code':103174,
            'identity':identity(code=103174), 'identity_hash':fingerprint(identity(code=103174)),
            'content_hash':'a'*64,'identity_document_id':'b'*64,'identity_content_hash':'c'*64,
            'source_url':'https://mutualfund.adityabirlacapital.com/fixture.pdf',
            'identity_source_url':'https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_fixture.pdf',
            'disclosure_as_of':'2026-08-31',
            **deepcopy(parsed())}
    for path in replay.PATHS:
        body[path]['extractor_version'] = version
    return {**body,'snapshot_id':fingerprint(body)}


def test_only_dependency_labels_differ_prior_snapshot_and_all_values_remain_exact():
    old, new = snapshot('6.20.1'), snapshot('6.21.4')
    saved = deepcopy(old)
    evidence = replay.comparison(old, new)
    assert evidence['retained_snapshot_id'] == old['snapshot_id']
    assert evidence['candidate_snapshot_id'] == new['snapshot_id']
    assert evidence['retained_snapshot_changed'] is False and old == saved
    assert old['fields'] == new['fields']
    assert replay.comparison(old, old) is None


@pytest.mark.parametrize('change', ['value','reason','identity','text','engine','policy','version','seal'])
def test_financial_identity_text_policy_or_unreviewed_transition_is_not_equivalent(change):
    old, new = snapshot('6.20.1'), snapshot('6.21.4')
    if change == 'value':
        new['fields']['base_expense_ratio']['value'] = '1.36'
    elif change == 'reason':
        new['fields']['total_expense_ratio']['reason'] = 'different_unavailable_reason'
    elif change == 'identity':
        new['identity_hash'] = 'f'*64
    elif change == 'text':
        new['detail_extraction']['text_hash'] = 'f'*64
    elif change == 'engine':
        new['detail_extraction']['extraction_engine_hash'] = 'f'*64
    elif change == 'policy':
        new['policy_hash'] = 'f'*64
    elif change == 'version':
        new['detail_extraction']['extractor_version'] = '99.0'
    if change != 'seal':
        new['snapshot_id'] = fingerprint({k:v for k,v in new.items() if k != 'snapshot_id'})
    else:
        new['snapshot_id'] = '0'*64
    with pytest.raises(Conflict):
        replay.comparison(old, new)
