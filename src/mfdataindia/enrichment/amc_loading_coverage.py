"""Account for the complete retained directory, never invent adapter readiness."""
from collections import Counter, defaultdict
from mfdataindia.enrichment import amc_register, book_sources, multi_facts
from mfdataindia.enrichment.evidence import normalized
from mfdataindia.enrichment.ingestion_discovery import State
from mfdataindia.enrichment.ingestion_gate import REASONS


def build(database_amcs, source_states, *, register=None, refresh_failure=None):
    register = register or amc_register.load_register()
    if not isinstance(register, amc_register.Register):
        raise ValueError('AMC_CENSUS_CONTRACT_INVALID')
    if type(database_amcs) is not list or type(source_states) is not dict:
        raise ValueError('AMC_COVERAGE_INPUT_INVALID')
    identities = defaultdict(list)
    for row in database_amcs:
        if type(row) is not dict or type(row.get('amfi_amc_name')) is not str or type(row.get('amc_id')) is not int:
            raise ValueError('AMC_DATABASE_IDENTITY_INVALID')
        identities[normalized(row['amfi_amc_name'])].append(row['amc_id'])
    adapters = defaultdict(list)
    for source in multi_facts.registry()['sources'] + book_sources.registry(4)['sources']:
        adapters[normalized(source['amc'])].append(source['key'])
    if set(source_states) - {k for group in adapters.values() for k in group}:
        raise ValueError('AMC_COVERAGE_UNKNOWN_SOURCE')
    rows=[]
    for member in register.members:
        key=normalized(member.name)
        matches=sorted(set(identities[key]))
        matching='MATCHED' if len(matches)==1 else 'UNMATCHED' if not matches else 'AMBIGUOUS'
        feeds=[]
        for name in sorted(adapters[key]):
            state=source_states.get(name)
            if state is None:
                feeds.append({'source_key':name,'state':'NOT_OBSERVED',
                              'latest_period_found':None,'latest_period_published':None})
                continue
            if (type(state) is not dict or state.get('state') not in {s.value for s in State}
                    or state.get('reason', 'DISCOVERY_NOT_SUPPORTED') not in REASONS
                    or set(state)-{'state','latest_period_found','latest_period_published','reason'}):
                raise ValueError('AMC_COVERAGE_SOURCE_STATE_INVALID')
            for label in ('latest_period_found','latest_period_published'):
                if state.get(label) is not None:
                    from datetime import date
                    if date.fromisoformat(state[label]).isoformat()!=state[label]:
                        raise ValueError('AMC_COVERAGE_PERIOD_INVALID')
            feeds.append({'source_key':name,**state})
        rows.append({'member_id':member.member_id,'name':member.name,
                     'identity_state':matching,'database_amc_ids':matches,
                     'adapter_state':'CONFIGURED' if feeds else 'UNSUPPORTED',
                     'automatic_eligibility':'IDENTITY_BLOCKED' if matching!='MATCHED' else
                          'CONFIGURED_ADAPTER_ONLY' if feeds else 'NOT_ELIGIBLE',
                     'directory_link_state':member.source_state,
                     'url_review_reasons':list(member.url_review_reasons),
                     'feeds':feeds,'directory_is_acquisition_authority':False})
    if refresh_failure is not None and refresh_failure not in ('FETCH_FAILED','INVALID_DIRECTORY_RESPONSE'):
        raise ValueError('AMC_CENSUS_REFRESH_REASON_INVALID')
    return {'format':'amc-loading-census-coverage-v1',
            'census_observed_at':register.observed_at.isoformat(),
            'census_sha256':register.directory_sha256,'population_basis':register.population_basis,
            'retained_census_visible':True,'refresh_state':refresh_failure or 'NOT_ATTEMPTED',
            'entries':rows,'total':len(rows),
            'identity_counts':dict(Counter(r['identity_state'] for r in rows)),
            'adapter_counts':dict(Counter(r['adapter_state'] for r in rows)),
            'all_amcs_current_or_complete':False,'data_quality_certified':False}
