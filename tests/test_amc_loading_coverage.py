from mfdataindia.enrichment import amc_register, amc_loading_coverage as coverage


def test_all57_retained_entries_remain_visible_with_separate_identity_adapter_states():
    register=amc_register.load_register()
    database=[{'amc_id':n+1,'amfi_amc_name':m.name} for n,m in enumerate(register.members)
              if m.name!='Nuvama Mutual Fund']
    states={'uti-expanded':{'state':'WAITING_FOR_PUBLICATION','latest_period_found':None,
                           'latest_period_published':'2026-09-30','reason':'CHECKED_ISSUER_CATALOGUE_EMPTY'}}
    result=coverage.build(database,states,refresh_failure='FETCH_FAILED')
    assert result['total']==len(result['entries'])==57
    assert {r['member_id'] for r in result['entries']}=={m.member_id for m in register.members}
    assert result['adapter_counts']=={'UNSUPPORTED':52,'CONFIGURED':5}
    assert result['identity_counts']=={'MATCHED':56,'UNMATCHED':1}
    assert result['refresh_state']=='FETCH_FAILED' and result['retained_census_visible']
    uti=next(r for r in result['entries'] if r['name']=='UTI Mutual Fund')
    assert uti['feeds'][0]['latest_period_published']=='2026-09-30'
    assert not result['all_amcs_current_or_complete']


def test_duplicate_database_identity_is_ambiguous_and_never_auto_eligible():
    result=coverage.build([{'amc_id':1,'amfi_amc_name':'UTI Mutual Fund'},
                           {'amc_id':2,'amfi_amc_name':'UTI Mutual Fund'}],{})
    uti=next(r for r in result['entries'] if r['name']=='UTI Mutual Fund')
    assert uti['identity_state']=='AMBIGUOUS'
    assert uti['automatic_eligibility']=='IDENTITY_BLOCKED'
