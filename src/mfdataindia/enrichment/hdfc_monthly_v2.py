"""HDFC's labelled monthly panels with actual dates, not a pinned edition.

Only the existing fixed Regular IDCW identity is supported. Ambiguous fields
remain unavailable; nothing borrows Direct BER, average AUM or a gauge risk.
The immutable v1 parser remains the retained September reference.
"""
import calendar
from datetime import date
import hashlib
from pathlib import Path
import re

from mfdataindia.enrichment import (hdfc_monthly, monthly_pdf, monthly_facts,
    multi_facts, idcw_discovery, hdfc_discovery)
from mfdataindia.enrichment.monthly_schemas import MonthlyFact
from mfdataindia.review.policy import fingerprint

NAME = 'HDFC Flexi Cap Fund'
DATE = r'([A-Za-z]+\s+[0-9]{1,2},\s*20[0-9]{2})'
MONTHS = {calendar.month_name[n].lower():n for n in range(1,13)}
one = hdfc_monthly.one


def printed_date(token):
    match = re.fullmatch(r'([A-Za-z]+)\s+([0-9]{1,2}),\s*(20[0-9]{2})',token)
    if match is None or match[1].lower() not in MONTHS:
        raise ValueError('HDFC_EXPLICIT_DATE_REQUIRED')
    return date(int(match[3]),MONTHS[match[1].lower()],int(match[2]))


def reporting_date(detail, continuation):
    text=' '.join(detail.split())
    expense=printed_date(one(r'EXPENSE RATIO \(As On '+DATE+r'\)',text))
    aum=printed_date(one(r'ASSETS UNDER MANAGEMENT € As on '+DATE,text))
    if expense!=aum or aum.day!=calendar.monthrange(aum.year,aum.month)[1]:
        raise ValueError('HDFC_REPORTING_DATE_NOT_CORROBORATED')
    for page in (detail,continuation):
        one(r'(?m)^\s*'+re.escape(NAME)+r'\s*$',page)
        footer=one(r'(?m)^\s*[1-9][0-9]{0,2}\s*\|\s*([A-Za-z]+\s+20[0-9]{2})\s*$',page)
        if footer!=calendar.month_name[aum.month]+' '+str(aum.year):
            raise ValueError('HDFC_FOOTER_REPORTING_DATE_DIFFERS')
    return aum


def fields(detail, continuation, as_of, *, detail_page=7):
    day=reporting_date(detail,continuation)
    if day.isoformat()!=as_of:
        raise ValueError('HDFC_ACTUAL_REPORTING_DATE_DIFFERS')
    text,notes=' '.join(detail.split()),' '.join(continuation.split())
    nav=printed_date(one(r'NAV \(As On '+DATE+r'\)',text))
    if (nav.year,nav.month)!=(day.year,day.month) or nav>day:
        raise ValueError('HDFC_NAV_CONTEXT_DIFFERS')
    printed=calendar.month_name[day.month]+' '+str(day.day)+', '+str(day.year)
    date_pattern=re.escape(printed).replace(r'\ ',r'\s+')
    values={}
    context={'fund_managers':[], 'direct_ber_not_selected':None,'monthly_average_aum_not_selected':None}
    def accept(name,value,unit,scope,raw,anchor,explicit=False):
        values[name]=MonthlyFact(status='accepted',value=value,unit=unit,scope=scope,
            disclosure_as_of=as_of,date_basis='explicit_field_date' if explicit else 'document_context_not_field_effective_date',
            field_effective_date=as_of if explicit else None,raw_value=raw,source_anchor=anchor,reason=None).model_dump(mode='json')
    def unavailable(name,reason):
        values[name]=MonthlyFact(status='unavailable',value=None,unit=None,scope=None,
            disclosure_as_of=as_of,date_basis='document_context_not_field_effective_date',
            field_effective_date=None,raw_value=None,source_anchor=None,reason=reason).model_dump(mode='json')
    try:
        if len(re.findall(r'(?<!#)#BENCHMARK INDEX',text))!=1:
            raise ValueError('HDFC_BENCHMARK_AMBIGUOUS')
        benchmark=one(r'#BENCHMARK INDEX (.*?) ##ADDL\. BENCHMARK INDEX',text)
        if not 1<=len(benchmark)<=500:
            raise ValueError('HDFC_BENCHMARK_AMBIGUOUS')
        accept('benchmark_name',benchmark,'name','fund',benchmark,
               f'physical page {detail_page}: #BENCHMARK INDEX, not additional benchmark')
    except ValueError:
        unavailable('benchmark_name','labelled_benchmark_missing_or_ambiguous')
    try:
        expense=one(r'EXPENSE RATIO \(As On '+date_pattern+r'\) (.*?) #BENCHMARK INDEX',text)
        if not expense.startswith('Base expense Ratio Including statutory levies on expenses part of BER, excluding brokerage, transaction cost and execution related statutory levies^'):
            raise ValueError('HDFC_BER_LABEL_REQUIRED')
        regular,direct=one(r'Regular: ([0-9]+\.[0-9]+)% Direct: ([0-9]+\.[0-9]+)%$',expense)
        from decimal import Decimal
        monthly_facts.decimal(regular,high=Decimal('10'))
        accept('base_expense_ratio',regular,'percent','regular_plan',expense,
               f'physical page {detail_page}: EXPENSE RATIO Regular, explicit BER label',True)
        context['direct_ber_not_selected']=direct
    except ValueError:
        unavailable('base_expense_ratio','explicit_regular_ber_missing_or_ambiguous')
    try:
        aum,average=one(r'ASSETS UNDER MANAGEMENT € As on '+date_pattern+r' Average for Month of '
            +re.escape(calendar.month_name[day.month])+r',\s*'+str(day.year)
            +r' ₹([0-9,]+\.[0-9]+)Cr\. ₹([0-9,]+\.[0-9]+)Cr\. QUANTITATIVE DATA',text)
        normalized=monthly_facts.decimal(aum)
        accept('aum',normalized,'INR_crore','fund','₹'+aum+'Cr.',
               f'physical page {detail_page}: As on {printed}, not monthly average',True)
        context['monthly_average_aum_not_selected']=average
    except ValueError:
        unavailable('aum','explicit_dated_fund_aum_missing_or_ambiguous')
    try:
        managers=one(r'FUND MANAGER ¥ Name Since Total Exp (.*?) DATE OF ALLOTMENT',text)
        row=re.compile(r"\s*([A-Za-z][A-Za-z .'\-]{1,100}?)(?:\s+\(([A-Za-z /\-]{1,60})\))?\s+"
            +DATE+r'\s+(?:Over\s+)?[0-9]+(?:\.[0-9]+)?\s+years\s*')
        names,roles,offset=[],[],0
        for matched in row.finditer(managers):
            if managers[offset:matched.start()].strip():
                raise ValueError('HDFC_COMPLETE_MANAGER_PANEL_REQUIRED')
            name,role,appointment=matched.groups()
            if printed_date(appointment)>day:
                raise ValueError('HDFC_MANAGER_APPOINTMENT_FUTURE')
            names.append(name.strip())
            roles.append({'name':name.strip(),'role':role or 'scheme_manager'})
            offset=matched.end()
        if managers[offset:].strip() or not 1<=len(names)<=9 or len(set(names))!=len(names):
            raise ValueError('HDFC_COMPLETE_MANAGER_PANEL_REQUIRED')
        overseas=one(r'Dedicated Fund Manager for Overseas Investments: Mr\. ([A-Za-z ]+) \(since '+DATE
            +r'\) \(Total Experience: (?:Over\s+)?[0-9]+(?:\.[0-9]+)? years\)',notes)
        overseas_name,appointment=overseas
        if printed_date(appointment)>day:
            raise ValueError('HDFC_MANAGER_APPOINTMENT_FUTURE')
        if overseas_name.strip() in names:
            raise ValueError('HDFC_MANAGER_PANEL_DUPLICATES')
        names.append(overseas_name.strip())
        roles.append({'name':overseas_name.strip(),'role':'dedicated_overseas_investments'})
        accept('fund_managers',names,'names','fund',managers+'; overseas: '+overseas_name,
               f'physical pages {detail_page}-{detail_page+1}: manager table plus dedicated overseas manager footnote; roles retained in snapshot')
        context['fund_managers']=roles
    except ValueError:
        unavailable('fund_managers','complete_manager_panel_missing_or_ambiguous')
    unavailable('risk_level','riskometer_pages_not_parsed_no_gauge_or_SSD_date_inference')
    return values,context


def select(extraction, observed):
    matches=[]
    pages=extraction['pages']
    for index,page in enumerate(pages[:-1]):
        if (re.search(r'(?m)^\s*'+re.escape(NAME)+r'\s*$',page['text'])
                and 'FUND MANAGER' in page['text'] and 'EXPENSE RATIO' in page['text']
                and 'ASSETS UNDER MANAGEMENT' in page['text']):
            continuation=pages[index+1]
            day=reporting_date(page['text'],continuation['text'])
            if day>observed.date() or 'Contd on next page' in continuation['text']:
                raise ValueError('HDFC_COMPLETE_NONFUTURE_FUND_PANELS_REQUIRED')
            matches.append((page,continuation,day))
    if len(matches)!=1:
        raise ValueError('HDFC_FUND_PANEL_AMBIGUOUS_OR_OUTSIDE_WINDOW')
    return matches[0]


def verify_extraction(value,raw,start,end):
    from importlib.metadata import version
    required={'source_hash','policy_hash','extractor_version','engine_hash','document_pages',
              'whole_document_coverage','pages','extraction_hash'}
    worker=Path(monthly_pdf.__file__)
    expected_engine=hashlib.sha256(worker.read_bytes()+worker.with_name('_monthly_pdf_worker.py').read_bytes()).hexdigest()
    if (type(value) is not dict or set(value)!=required
            or value['source_hash']!=hashlib.sha256(raw).hexdigest()
            or value['policy_hash']!=fingerprint(monthly_pdf.policy())
            or value['engine_hash']!=expected_engine or value['extractor_version']!=version('pypdf')
            or type(value['document_pages']) is not int or not end<=value['document_pages']<=400
            or value['whole_document_coverage'] is not False
            or type(value['pages']) is not list or len(value['pages'])!=end-start+1
            or value['extraction_hash']!=fingerprint({k:v for k,v in value.items() if k!='extraction_hash'})):
        raise ValueError('HDFC_BOUNDED_EXTRACTION_BINDING_DIFFERS')
    for number,page in enumerate(value['pages'],start):
        if (type(page) is not dict or set(page)!={'page','text','text_hash'} or page['page']!=number
                or type(page['text']) is not str or len(page['text'])>200000
                or page['text_hash']!=hashlib.sha256(page['text'].encode()).hexdigest()):
            raise ValueError('HDFC_BOUNDED_PAGE_EVIDENCE_DIFFERS')
    if sum(len(p['text']) for p in value['pages'])>2000000:
        raise ValueError('HDFC_BOUNDED_PAGE_EVIDENCE_DIFFERS')


def derive(identity, book, summary, *, book_url, listing, observed, extractor=monthly_pdf.extract):
    config=multi_facts.source()
    if identity!=config['identity'] or hashlib.sha256(summary).hexdigest()!=config['identity_source']['sha256']:
        raise ValueError('HDFC_CURRENT_EXACT_IDENTITY_REQUIRED')
    discovery=hdfc_discovery.discover(listing,observed)
    if discovery.files!=(('book',book_url),):
        raise ValueError('HDFC_ISSUER_LINK_REQUIRED')
    extraction=extractor(book,start=1,end=24)
    verify_extraction(extraction,book,1,24)
    first,second,day=select(extraction,observed)
    identity_extraction=extractor(summary,start=1,end=1)
    verify_extraction(identity_extraction,summary,1,1)
    identity_evidence=idcw_discovery.registry_identity(identity,identity_extraction['pages'][0]['text'])
    values,context=fields(first['text'],second['text'],day.isoformat(),detail_page=first['page'])
    if not any(v['status']=='accepted' for v in values.values()):
        raise ValueError('HDFC_NO_USABLE_VALIDATED_FIELDS')
    context['discovery']={'listing_url':hdfc_discovery.LISTING,'listing_sha256':hashlib.sha256(listing).hexdigest(),
        'actual_reporting_date':day.isoformat(),'identity_document_date_is_not_monthly_fact_date':True}
    from mfdataindia.enrichment import hdfc_source_v2
    body={'format':'registered-monthly-facts-v1','source_key':config['key'],'code':identity['code'],
        'identity':identity,'identity_hash':fingerprint(identity),'content_hash':hashlib.sha256(book).hexdigest(),
        'identity_content_hash':hashlib.sha256(summary).hexdigest(),'source_url':book_url,
        'identity_source_url':config['identity_source']['url'],'disclosure_as_of':day.isoformat(),
        'fields':values,'identity_evidence':identity_evidence,'reported_context':context,
        'selected_panel_pages':[first['page'],second['page']],
        'extraction':{**extraction,'pages':[{k:v for k,v in p.items() if k!='text'} for p in extraction['pages']]},
        'identity_extraction':{**identity_extraction,'pages':[{k:v for k,v in p.items() if k!='text'} for p in identity_extraction['pages']]},
        'policy_hash':fingerprint(hdfc_source_v2.policy()),'engine_hash':engine_hash(),
        'verification_status':'needs_verification','canonical_analytics_replaced':False}
    return {**body,'snapshot_id':fingerprint(body)}


def engine_hash():
    digest=hashlib.sha256()
    for name in ('hdfc_monthly_v2.py','hdfc_source_v2.py','hdfc_discovery.py','hdfc_monthly.py','multi_facts.py',
                 'idcw_discovery.py','monthly_pdf.py','_monthly_pdf_worker.py','monthly_schemas.py','schemas.py','evidence.py'):
        raw=Path(__file__).with_name(name).read_bytes()
        digest.update(name.encode()+b'\0'+len(raw).to_bytes(8,'big')+raw)
    return digest.hexdigest()
