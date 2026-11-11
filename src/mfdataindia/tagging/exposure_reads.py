"""Read existing enrolled sealed portfolio evidence; never install or publish."""
from datetime import date

from mfdataindia.datasets import policy as datasets, reads as dataset_reads
from mfdataindia.enrichment import portfolio_store
from mfdataindia.review import store as review_store, reader_access_profile as profiles
from mfdataindia.review.policy import fingerprint
from . import exposure_policy as p, entity_registry, reads as tags

PREFIX = '/api/fund-exposures/v1'


def _checked(conn):
    entry, _ = dataset_reads._locked(conn, datasets.dataset_id('mf_portfolios', 'snapshots'))
    p.require(entry['schema'] == 'mf_portfolios' and entry['relation'] == 'snapshots'
              and entry['owner'] == 'mfdata_admin' and entry['key_source'] == 'physical_primary_key'
              and entry['key_columns'] == ['snapshot_id']
              and [(c['name'],c['type']) for c in entry['columns']] == [
                  ('snapshot_id','text'),('amfi_scheme_code','int4'),('source_id','text'),
                  ('identity_source_id','text'),('policy_hash','text'),('disclosure_as_of','date'),
                  ('document','jsonb')], 'EXPOSURE_READ_MODEL_DIFFERS')
    # Keep the existing policy, raw-source, trigger/function and generator
    # coexistence checks. Their catalogue work is constant per request, not
    # per holding; native evidence measures this without security caching.
    portfolio_store.verify_acl(conn,reader_profile=profiles.BROAD)
    kind = conn.execute("SELECT relkind::text AS kind FROM pg_class WHERE oid='mf_portfolios.snapshots'::regclass").fetchone()
    p.require(kind and kind['kind']=='r','EXPOSURE_READ_MODEL_DIFFERS')


def _derive(conn, code):
    p.require(type(code) is int and 0 < code < 2**31, 'INVALID_EXPOSURE_QUERY')
    _checked(conn)
    try:
        state, document = portfolio_store.get(conn,code)
    except review_store.NotFound:
        raise p.TaggingError('EXPOSURE_FUND_NOT_FOUND') from None
    if state['state'] != 'available':
        return state['state'], state['reason'], None
    # Date comes from the same transaction, not a user-supplied freshness flag.
    observed = conn.execute('SELECT CURRENT_DATE::text AS today').fetchone()['today']
    date.fromisoformat(observed)
    return 'available', None, p.derive(document,entity_registry.load(),observed_on=observed)


def discovery(conn):
    _checked(conn)
    row = conn.execute('SELECT count(*) AS snapshots,min(disclosure_as_of)::text AS first_date,max(disclosure_as_of)::text AS last_date FROM mf_portfolios.snapshots').fetchone()
    registry = entity_registry.load()
    p.validate_registry(registry)
    return tags.bounded({'format':p.VERSION,'source_format':'official-complete-portfolio-v1',
        'source_enrollment_verified':True,'retained_snapshots':row['snapshots'],
        'first_reporting_date':row['first_date'],'last_reporting_date':row['last_date'],
        'coverage_basis':'retained_snapshots_not_current_universe_coverage',
        'entity_registry_format':registry['format'],'entity_registry_hash':fingerprint(registry),
        'entity_mapping_records':len(registry['records']),
        'sections':['positions','securities','entities','attributes'],'funds_url':PREFIX+'/funds/{code}',
        'related_news_requires_verified_bridge':True,'absence_or_exclusion_certified':False,
        'freshness_certified':False})


def summary(conn, code):
    state, reason, result = _derive(conn,code)
    metadata = None if result is None else {k:v for k,v in result.items()
                                            if k not in ('positions','entities','attributes','securities')}
    return tags.bounded({'format':p.VERSION,'code':code,'state':state,'reason':reason,
        'metadata':metadata,'entity_count':0 if result is None else len(result['entities']),
        'attribute_count':0 if result is None else len(result['attributes']),
        'security_count':0 if result is None else len(result['securities']),
        'position_count':0 if result is None else len(result['positions']),
        'related_news_linkage':'requires_verified_article_entity_evidence_in_TI',
        'exact_scheme_news_unchanged':True})


def page(conn, code, section, *, limit=50, assessment_id=None, after=None):
    p.require(section in ('positions','entities','attributes','securities')
              and type(limit) is int and 1 <= limit <= 50,
              'INVALID_EXPOSURE_QUERY')
    p.require((assessment_id is None and after is None) or
              (type(assessment_id) is str and p.HASH.fullmatch(assessment_id)
               and type(after) is str and p.HASH.fullmatch(after)), 'INVALID_EXPOSURE_QUERY')
    state, reason, result = _derive(conn,code)
    if result is None:
        p.require(assessment_id is None,'EXPOSURE_ASSESSMENT_CHANGED')
        return tags.bounded({'format':p.VERSION,'code':code,'state':state,'reason':reason,
            'section':section,'assessment_id':None,'portfolio_as_of':None,'items':[],
            'next_after':None,'limit':limit,'absence_or_exclusion_certified':False})
    p.require(assessment_id is None or assessment_id == result['assessment_id'],'EXPOSURE_ASSESSMENT_CHANGED')
    items = sorted([{'item_id':fingerprint({'section':section,'item':item}),**item}
                    for item in result[section]],key=lambda item:item['item_id'])
    p.require(after is None or any(item['item_id']==after for item in items),'INVALID_EXPOSURE_QUERY')
    selected = [item for item in items if after is None or item['item_id'] > after][:limit+1]
    return tags.bounded({'format':p.VERSION,'code':code,'state':state,'reason':reason,
        'section':section,'assessment_id':result['assessment_id'],
        'portfolio_as_of':result['portfolio_as_of'],'items':selected[:limit],
        'next_after':selected[limit-1]['item_id'] if len(selected)>limit else None,
        'limit':limit,'absence_or_exclusion_certified':False})
