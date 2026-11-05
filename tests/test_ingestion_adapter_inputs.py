"""Discovery cache keys preserve input roles, not mutable catalogue timestamps.

Only this unit's scope reader is doubled. Real scope/install/publication/API
semantics are exercised by the hosted current-main integration case.
"""
import copy
from datetime import datetime,timezone
import hashlib
import json

from mfdataindia.enrichment import ingestion_adapters as adapters, ingestion_discovery as d
from mfdataindia.enrichment import book_sources,kotak_book
from mfdataindia.review.policy import fingerprint
from test_kotak_method_postgres import inputs

NOW=datetime(2026,11,5,tzinfo=timezone.utc)


def test_catalogue_nonce_does_not_create_financial_work_but_revised_pdf_does(monkeypatch):
    identity={'code':112090,'amc':kotak_book.AMC,'scheme_name':'Kotak Flexicap Fund - Regular Plan - Growth',
              'plan':'REGULAR','option':'GROWTH','isins':['INF174K01336']}
    with book_sources.select_registry(7):
        _,originals,_=inputs([identity],method=2)
    source={o['source_url']:copy.deepcopy(o) for o in originals}
    def scope(self):
        self.maximum=80*1048576
        self.spec=book_sources.specification('kotak-expanded')
        self.population_hash='b'*64
        self.derivation_version='c'*64
    monkeypatch.setattr(adapters.Adapter,'_scope',scope)
    class Store:
        saved=None
        business=None
        def prior_inputs(self,source,seed,derivation,population):
            return self.saved if self.business and all(row in self.business for row in seed) else None
    store=Store()
    def transport(url,**kwargs):
        return copy.deepcopy(source[url])
    def new():
        return adapters.Adapter('kotak-expanded','tlws-mf-official-facts-refresh-dev-synthetic',None,store,
            now=NOW,transport=transport)
    first=new()
    _,candidate=first.discover(lambda:None)
    assert not candidate.complete
    for original in originals:
        first.fetch(original['source_url'])
    first._identity_roles()
    store.business=d.manifest(first._documents())
    store.saved={'inputs':list(first._inputs()),'population_hash':candidate.population_hash}
    old_observation=fingerprint(first._inputs())
    for original in source.values():
        if original['role']=='catalogue':
            parsed=json.loads(original['content'])
            parsed['request_timestamp']='synthetic-new-discovery-observation'
            original['content']=json.dumps(parsed).encode()
    second=new()
    _,same=second.discover(lambda:None)
    assert same.complete and d.manifest(same.originals)==store.business
    assert fingerprint(same.inputs)!=old_observation
    old_work=d.work('kotak-expanded','2026-10-31',store.business,'c'*64,candidate.population_hash)
    assert old_work==d.work('kotak-expanded','2026-10-31',d.manifest(same.originals),'c'*64,same.population_hash)
    current=next(url for url in source if '/Factsheet-for-October-' in url)
    source[current]['content']+=b'-actual-revision'
    _,changed=new().discover(lambda:None)
    assert not changed.complete
    assert hashlib.sha256(source[current]['content']).hexdigest() in {r['sha256'] for r in d.manifest(changed.originals)}


def test_active_passive_role_swap_is_not_an_identical_input_set():
    one=d.manifest([{'role':'current_active','content':b'%PDF-A'},
                    {'role':'current_passive','content':b'%PDF-P'}])
    swapped=d.manifest([{'role':'current_active','content':b'%PDF-P'},
                        {'role':'current_passive','content':b'%PDF-A'}])
    assert one!=swapped
    assert d.work('uti-expanded','2026-10-31',one,'a'*64,'b'*64)['work_id']!=d.work('uti-expanded','2026-10-31',swapped,'a'*64,'b'*64)['work_id']
