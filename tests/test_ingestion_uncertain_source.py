"""Actual orchestration/claim boundary with unresolved-source fault injection."""
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

from psycopg.pq import TransactionStatus
import pytest

from mfdataindia.enrichment import ingestion_gate as gate,ingestion_runner as runner
from mfdataindia.enrichment.ingestion_discovery import work,manifest,State
from test_ingestion_runner import Adapter,Store,SOURCE,EXECUTION


class Control:
    """Response fixture only; native tests establish the SQL semantics."""
    def __init__(self,candidate,prior):
        self.info=SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.candidate,self.prior=candidate,prior
        self.claim_inserts=0

    def transaction(self):
        return nullcontext()

    def execute(self,query,args=None):
        text=str(query)
        if text.startswith('INSERT INTO mf_ingestion_ops.claims'):
            self.claim_inserts+=1
        if text.startswith('SELECT document FROM mf_ingestion_ops.work'):
            value={'document':self.candidate}
        elif 'FROM mf_ingestion_ops.claims c' in text:
            value={'document':self.prior}
        else:
            value=None
        return SimpleNamespace(fetchone=lambda:value,fetchall=lambda:[])


@pytest.mark.parametrize('revision',['document','period','derivation'])
def test_new_candidate_does_not_bypass_unresolved_publication_of_its_source(revision):
    old=Adapter(raw=b'%PDF-original')
    period='2026-09-30'
    candidate=work(SOURCE,period,manifest(old.candidate.originals),'a'*64,'b'*64)
    prior={'format':'amc-ingestion-claim-v1','source_key':SOURCE,
           'work_id':candidate['work_id'],'execution_id':EXECUTION+'-prior',
           'attempt':1,'source_inputs':[],'claim_id':'d'*64}
    selected=Adapter(raw=b'%PDF-original')
    if revision=='document':
        selected.candidate.originals[0]['content']=b'%PDF-revised'
    elif revision=='period':
        period='2026-10-31'
    else:
        selected.candidate=replace(selected.candidate,derivation_version='c'*64)
    real_prepare=selected.prepare
    selected.prepare=lambda value,deadline:replace(real_prepare(value,deadline),actual_period=period)
    expected=work(SOURCE,period,manifest(selected.candidate.originals),
                  selected.candidate.derivation_version,selected.candidate.population_hash)
    assert expected['work_id']!=candidate['work_id']
    control=Control(expected,prior)
    class ActualClaims(Store):
        def claim(self,value,execution,**kwargs):
            return gate.claim(control,value,execution,**kwargs)
    store=ActualClaims()
    observed=runner.execute(SOURCE,EXECUTION+'-new',selected,store)
    assert observed['state']==State.HELD.value
    assert observed['claim_execution_id']==prior['execution_id']
    assert observed['disposition']=='INSPECT_ONLY_NO_REPLAY'
    assert store.submissions==control.claim_inserts==0
