"""Retained real September panels plus explicitly synthetic date/layout variations."""
from pathlib import Path
import json
from mfdataindia.enrichment import hdfc_monthly_v2 as method
from mfdataindia.enrichment import hdfc_monthly as predecessor
import pytest

ROOT=Path(__file__).resolve().parents[1]
PANELS=json.loads((ROOT/'tests/fixtures/hdfc/september-2026-panels.json').read_bytes())


def panels(*,october=False):
    texts=[p['text'] for p in PANELS['pages']]
    if october:
        texts=[t.replace('September 30, 2026','October 31, 2026')
                .replace('SEPTEMBER 30, 2026','OCTOBER 31, 2026')
                .replace('September 2026','October 2026')
                .replace('September, 2026','October, 2026') for t in texts]
    return texts


def test_synthetic_next_month_uses_explicit_printed_dates_not_a_pinned_september_edition():
    fields,context=method.fields(*panels(october=True),'2026-10-31')
    assert fields['aum']['value']=='108546.98'
    assert fields['base_expense_ratio']['value']=='1.27'
    assert fields['aum']['field_effective_date']=='2026-10-31'
    assert fields['fund_managers']['value']==['Amit Ganatra','Bhagyesh Kagalkar','Gopal Agrawal']
    assert context['monthly_average_aum_not_selected']=='111,285.67'


def test_retained_real_september_panels_preserve_every_field_and_manager_role():
    actual=method.fields(*panels(),'2026-09-30')
    assert actual==predecessor.fields(*panels(),'2026-09-30')


@pytest.mark.parametrize('defect',['wrong_period','aum_date','footer','nav_future'])
def test_inconsistent_or_nonactual_reporting_date_is_rejected(defect):
    detail,continuation=panels()
    period='2026-09-30'
    if defect=='wrong_period':
        period='2026-10-31'
    elif defect=='aum_date':
        detail=detail.replace('As on September 30, 2026','As on August 31, 2026')
    elif defect=='footer':
        continuation=continuation.replace('September 2026','August 2026')
    else:
        detail=detail.replace('SEPTEMBER 30, 2026','OCTOBER 31, 2026')
    with pytest.raises(ValueError):
        method.fields(detail,continuation,period)


@pytest.mark.parametrize('defect,field',[
    ('ter','base_expense_ratio'),('unlabelled_aum','aum'),
    ('extra_manager','fund_managers'),('missing_overseas','fund_managers'),
    ('future_manager','fund_managers'),('ambiguous_benchmark','benchmark_name')])
def test_unsafe_field_is_unavailable_without_borrowing_another_value(defect,field):
    detail,continuation=panels()
    if defect=='ter':
        detail=detail.replace('Base expense Ratio','Total expense Ratio')
    elif defect=='unlabelled_aum':
        detail=detail.replace('₹108,546.98Cr.','₹108,546.98')
    elif defect=='extra_manager':
        detail=detail.replace('DATE OF ALLOTMENT','Unknown unparsed extra manager DATE OF ALLOTMENT')
    elif defect=='missing_overseas':
        continuation=continuation.replace('Dedicated Fund Manager for Overseas Investments','Unknown role')
    elif defect=='future_manager':
        detail=detail.replace('February 01, 2026','February 01, 2027')
    else:
        detail=detail.replace('##ADDL. BENCHMARK INDEX','#BENCHMARK INDEX extra ##ADDL. BENCHMARK INDEX')
    result,_=method.fields(detail,continuation,'2026-09-30')
    assert result[field]['status']=='unavailable' and result[field]['value'] is None
    assert result['risk_level']['value'] is None


def test_dynamic_source_authority_keeps_the_exact_identity_but_not_september_values():
    from mfdataindia.enrichment import hdfc_source_v2,multi_facts
    source=hdfc_source_v2.source()
    assert source['identity']==multi_facts.source()['identity']
    assert source['identity_source']==multi_facts.source()['identity_source']
    assert 'edition' not in source and 'reviewed_values' not in source
    assert hdfc_source_v2.policy()['bounds']['hdfc_requests']==3
    assert source['parser_engine_hash']==method.engine_hash()


def test_current_method_derives_actual_date_and_requires_issuer_link_and_fixed_identity():
    from test_hdfc_dynamic_postgres import inputs,NOW
    from mfdataindia.enrichment import multi_facts,hdfc_discovery
    import copy
    originals,extract,_=inputs()
    url=next(u for u in originals if 'Factsheet' in u)
    book=originals[url]['content']
    identity_url=multi_facts.source()['identity_source']['url']
    summary=originals[identity_url]['content']
    args={'book_url':url,'listing':originals[hdfc_discovery.LISTING]['content'],'observed':NOW,'extractor':extract}
    result=method.derive(multi_facts.source()['identity'],book,summary,**args)
    assert result['disclosure_as_of']=='2026-10-31'
    assert result['fields']['aum']['value']=='108546.98'
    assert result['reported_context']['discovery']['actual_reporting_date']=='2026-10-31'
    for change in ('identity','issuer_link','identity_bytes','extraction'):
        identity=copy.deepcopy(multi_facts.source()['identity'])
        options=dict(args)
        raw=summary
        if change=='identity':
            identity['plan']='DIRECT'
        elif change=='issuer_link':
            options['listing']=b'<a href="https://unrelated.invalid/test.pdf">not an issuer book</a>'
        elif change=='identity_bytes':
            raw+=b'changed'
        else:
            def bad_extract(*a,**kw):
                v=extract(*a,**kw)
                v['source_hash']='f'*64
                return v
            options['extractor']=bad_extract
        with pytest.raises(ValueError):
            method.derive(identity,book,raw,**options)


@pytest.mark.parametrize('defect',['source','policy','engine','version','page_hash','page_number','seal','page_count'])
def test_actual_worker_evidence_binding_is_not_a_source_hash_only_check(defect):
    from test_hdfc_dynamic_postgres import inputs
    originals,extract,_=inputs()
    raw=next(v['content'] for k,v in originals.items() if 'Factsheet' in k)
    value=extract(raw,start=1,end=24)
    if defect in ('source','policy','engine'):
        value[defect+'_hash']='f'*64
    elif defect=='version':
        value['extractor_version']='unknown'
    elif defect=='page_hash':
        value['pages'][0]['text_hash']='f'*64
    elif defect=='page_number':
        value['pages'][0]['page']=0
    elif defect=='seal':
        value['extraction_hash']='f'*64
    else:
        value['document_pages']=23
    with pytest.raises(ValueError):
        method.verify_extraction(value,raw,1,24)
