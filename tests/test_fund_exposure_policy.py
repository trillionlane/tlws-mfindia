"""Observed exposure tests use retained parser fixtures, never live sources."""
from copy import deepcopy
from decimal import localcontext

import pytest

from mfdataindia.enrichment import portfolios
from mfdataindia.review.policy import fingerprint
from mfdataindia.tagging import exposure_policy as p
from mfdataindia.tagging.policy import TaggingError
from test_complete_portfolios import parsed
from test_official_facts import identity


def document():
    who = identity()
    body = {'format':'official-complete-portfolio-v1','code':1,'identity':who,
        'identity_hash':fingerprint(who),'source_id':'1'*64,'identity_source_id':'2'*64,
        'content_hash':'3'*64,'identity_content_hash':'4'*64,
        'source_url':'https://mutualfund.adityabirlacapital.com/-/media/bsl/files/resources/monthly-portfolio/synthetic.zip',
        'identity_source_url':'https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_synthetic.pdf',
        'disclosure_as_of':'2026-08-31','sheet':'BSLEQTY','member_name':'synthetic.xls',
        'member_hash':'5'*64,'extractor_version':'synthetic-retained-parser-fixture',
        'identity_extraction':{},'sheet_cells_hash':'6'*64,'engine_code_hash':portfolios.engine_hash(),
        'policy_hash':fingerprint(portfolios.policy()), **parsed()}
    body['holdings_count'] = len(body['holdings'])
    return {**body,'snapshot_id':fingerprint(body)}


def reseal(body):
    body['snapshot_id'] = fingerprint({k:v for k,v in body.items() if k != 'snapshot_id'})
    return body


def registry():
    def attribute(dimension,value):
        return {'dimension':dimension,'value':value,'valid_from':'2026-01-01',
                'valid_until':None,'evidence_hash':'a'*64}
    return {'format':p.REGISTRY_VERSION,'records':[
        {'security_id':isin,'entity_id':entity,'entity_kind':'company','state':'verified','valid_from':'2026-01-01',
         'valid_until':None,'evidence_hash':'b'*64,
         'attributes':[attribute('sector','financials'),attribute('issuer-domicile','in'),
                       attribute('theme','digital-payments')]}
        for isin,entity in [('INE090A01021','synthetic:issuer-one'),('INE040A01034','synthetic:issuer-two')]]}


def derive(body=None,records=None,**kwargs):
    return p.derive(body or document(),registry() if records is None else records,
                    observed_on=kwargs.pop('observed_on','2026-11-10'),**kwargs)


def test_dated_positive_exposures_preserve_original_denominator_and_unknown_absence():
    before = document()
    result = derive(before)
    assert result['code'] == 1 and result['portfolio_as_of'] == '2026-08-31'
    assert result['mapped_holding_count'] == 2 and result['holdings_count'] == 5
    assert result['withheld_row_reasons'] == {'NON_ISSUER_CASH_OR_NET_BALANCE':3}
    assert [x['weight']['lower'] for x in result['entities']] == ['50.0','40.0']
    sector = next(x for x in result['attributes'] if x['dimension']=='sector')
    assert sector['weight']['lower'] == sector['weight']['upper'] == '90.0'
    assert sector['weight']['unit'] == 'percent_of_original_disclosed_AUM'
    assert result['absence_or_exclusion_certified'] is result['freshness_certified'] is False
    assert result['lookthrough_performed'] is False and result['evidence_age_days'] == 71
    assert result['originals_replaced'] is False and document() == before
    assert result == derive(before)


def test_empty_registry_retains_every_unknown_without_name_inference():
    result = derive(records={'format':p.REGISTRY_VERSION,'records':[]})
    assert result['entities'] == result['attributes'] == []
    assert result['mapped_holding_count'] == 0
    assert result['withheld_row_reasons']['SECURITY_ENTITY_NOT_MAPPED_AT_DATE'] == 2
    assert result['absence_or_exclusion_certified'] is False


@pytest.mark.parametrize('change',['ambiguous','duplicate','expired','future'])
def test_mapping_conflict_or_wrong_date_withholds_only_affected_position(change):
    evidence = registry()
    record = evidence['records'][0]
    if change == 'ambiguous':
        record['state'] = 'ambiguous'
    elif change == 'duplicate':
        evidence['records'].append(deepcopy(record))
    elif change == 'expired':
        record['valid_until'] = '2026-08-30'
    else:
        record['valid_from'] = '2026-09-01'
    result = derive(records=evidence)
    assert [x['entity_id'] for x in result['entities']] == ['synthetic:issuer-two']
    assert sum(result['withheld_row_reasons'].values()) == 4


def test_censored_weight_is_interval_not_zero_and_ambient_decimal_context_is_irrelevant():
    body = document()
    body['holdings'][0]['weight_percent'] = {'value':None,'basis':'censored',
        'upper_bound_exclusive':'0.01','raw':'$0.00%'}
    body['holdings'][1]['weight_percent']['value'] = '89.995'
    reseal(body)
    result = derive(body)
    first = result['entities'][0]['weight']
    assert first == {'lower':'0','upper':'0.01','upper_exclusive':True,
                     'unit':'percent_of_original_disclosed_AUM'}
    with localcontext() as context:
        context.prec = 2
        assert derive(body) == result


def test_registry_and_document_revisions_have_different_evidence_identities():
    first = derive()
    evidence = registry()
    evidence['records'][0]['evidence_hash'] = 'c'*64
    second = derive(records=evidence)
    assert first['assessment_id'] != second['assessment_id']
    assert first['registry_hash'] != second['registry_hash']
    body = document()
    body['content_hash'] = 'd'*64
    third = derive(reseal(body))
    assert first['portfolio_snapshot_id'] != third['portfolio_snapshot_id']
    assert first['assessment_id'] != third['assessment_id']


def test_single_valued_attribute_conflict_is_withheld_but_the_entity_is_retained():
    evidence = registry()
    other = deepcopy(evidence['records'][0]['attributes'][1])
    other['value'] = 'us'
    evidence['records'][0]['attributes'].append(other)
    result = derive(records=evidence)
    country = next(x for x in result['attributes'] if x['dimension']=='issuer-domicile')
    assert country['weight']['lower'] == '40.0'
    assert result['mapped_holding_count'] == 2
    assert result['withheld_row_reasons']['ENTITY_ATTRIBUTE_CONFLICT'] == 1


@pytest.mark.parametrize('change',['hash','identity','policy','count','row','date','weight','extra'])
def test_malformed_or_inconsistent_snapshot_fails_with_safe_label(change):
    body = document()
    if change == 'hash':
        body['snapshot_id'] = '0'*64
    elif change == 'identity':
        body['identity_hash'] = '0'*64
    elif change == 'policy':
        body['policy_hash'] = '0'*64
    elif change == 'count':
        body['holdings_count'] += 1
    elif change == 'row':
        body['holdings'][1]['row'] = body['holdings'][0]['row']
    elif change == 'date':
        body['disclosure_as_of'] = '2027-01-01'
    elif change == 'weight':
        body['holdings'][0]['weight_percent']['value'] = 'NaN'
    else:
        body['synthetic-secret-marker'] = 'postgresql://do-not-print'
    if change != 'hash':
        reseal(body)
    with pytest.raises(TaggingError) as error:
        derive(body)
    assert str(error.value) in {'EXPOSURE_PORTFOLIO_DIFFERS','EXPOSURE_WEIGHT_DIFFERS'}
    assert 'synthetic-secret-marker' not in str(error.value)


@pytest.mark.parametrize('value',['postgresql://synthetic-secret-marker','NOT-AN-ISIN','INE090A01022'])
def test_invalid_registry_identifiers_are_rejected_without_values(value):
    evidence = registry()
    evidence['records'][0]['security_id'] = value
    with pytest.raises(TaggingError,match='^EXPOSURE_REGISTRY_DIFFERS$'):
        derive(records=evidence)


def test_cross_security_entity_attribute_conflict_cannot_be_resolved_per_row():
    evidence = registry()
    evidence['records'][1]['entity_id'] = evidence['records'][0]['entity_id']
    evidence['records'][1]['attributes'][1]['value'] = 'us'
    result = derive(records=evidence)
    assert not any(x['dimension']=='issuer-domicile' for x in result['attributes'])
    assert len(result['entities']) == 1 and result['mapped_holding_count'] == 2


def test_complete_statement_with_inconsistent_weights_is_not_exposure_evidence():
    body = document()
    body['holdings'][0]['weight_percent']['value'] = '1.0'
    with pytest.raises(TaggingError,match='^EXPOSURE_PORTFOLIO_DIFFERS$'):
        derive(reseal(body))


def test_boolean_code_is_not_an_integer_scheme_identity():
    body = document()
    body['code'] = True
    with pytest.raises(TaggingError,match='^EXPOSURE_PORTFOLIO_DIFFERS$'):
        derive(reseal(body))


def test_exact_securities_remain_observed_when_issuer_registry_is_missing():
    result = derive(records={'format':p.REGISTRY_VERSION,'records':[]})
    assert len(result['securities']) == 2 and result['entities'] == []
    assert {x['security_id'] for x in result['securities']} == {'INE090A01021','INE040A01034'}
    assert {x['weight']['lower'] for x in result['securities']} == {'50.0','40.0'}
    assert result['absence_or_exclusion_certified'] is False


def test_fund_entity_does_not_claim_underlying_country_sector_or_theme():
    evidence = registry()
    evidence['records'][0]['entity_kind'] = 'fund'
    result = derive(records=evidence)
    assert result['entities'][0]['entity_kind'] == 'fund'
    assert all(x['weight']['lower']=='40.0' for x in result['attributes'])
    assert result['withheld_row_reasons']['FUND_LOOKTHROUGH_UNAVAILABLE'] == 1
    assert result['lookthrough_performed'] is False


def test_conflicting_entity_kinds_withhold_that_entity_without_losing_observed_security():
    evidence = registry()
    evidence['records'][1]['entity_id'] = evidence['records'][0]['entity_id']
    evidence['records'][1]['entity_kind'] = 'fund'
    result = derive(records=evidence)
    assert len(result['securities']) == 2 and result['entities'] == result['attributes'] == []
    assert result['withheld_row_reasons']['ENTITY_KIND_CONFLICT'] == 2


def test_partial_statement_cannot_enter_the_complete_portfolio_adapter():
    body = document()
    body['statement_complete'] = False
    with pytest.raises(TaggingError,match='^EXPOSURE_PORTFOLIO_DIFFERS$'):
        derive(reseal(body))


def test_dated_entity_fact_applies_to_all_verified_holdings_of_that_entity():
    evidence = registry()
    evidence['records'][1]['entity_id'] = evidence['records'][0]['entity_id']
    evidence['records'][1]['attributes'] = []
    result = derive(records=evidence)
    assert all(a['weight']['lower']=='90.0' for a in result['attributes'])


def test_active_conflicting_entity_fact_on_unheld_security_still_withholds_attribute():
    evidence = registry()
    record = deepcopy(evidence['records'][0])
    record['security_id'] = 'INE009A01021'
    record['attributes'][1]['value'] = 'us'
    evidence['records'].append(record)
    result = derive(records=evidence)
    country = next(a for a in result['attributes'] if a['dimension']=='issuer-domicile')
    assert country['weight']['lower']=='40.0'
    assert result['withheld_row_reasons']['ENTITY_ATTRIBUTE_CONFLICT']==1


def test_retained_engine_identity_is_preserved_not_rewritten_to_current_runtime():
    body = document()
    body['engine_code_hash'] = 'e'*64
    result = derive(reseal(body))
    assert result['portfolio_engine_hash']=='e'*64
    assert result['portfolio_snapshot_id']==body['snapshot_id']


def test_monetary_lakh_values_are_not_limited_by_a_percentage_cap():
    from decimal import Decimal
    body = document()
    for holding in body['holdings']:
        holding['market_value_inr_lakh'] = format(Decimal(holding['market_value_inr_lakh'])*100000,'f')
    for subtotal in body['subtotals']:
        subtotal['market_value_inr_lakh'] = format(Decimal(subtotal['market_value_inr_lakh'])*100000,'f')
    body['grand_total_inr_lakh'] = '10000000.00'
    result = derive(reseal(body))
    assert [item['weight']['lower'] for item in result['entities']]==['50.0','40.0']


def test_tiny_reported_weight_retains_frozen_numeric_precision_and_is_not_censored():
    body = document()
    body['holdings'][0]['weight_percent']['value'] = '0.'+'0'*96+'1'
    body['holdings'][1]['weight_percent']['value'] = '90.0'
    result = derive(reseal(body))
    assert result['entities'][0]['weight']['lower'] == '0.'+'0'*96+'1'
    assert result['entities'][0]['weight']['upper_exclusive'] is False
