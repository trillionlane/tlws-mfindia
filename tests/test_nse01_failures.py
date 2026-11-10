"""Actual CLI/publication boundaries, synthetic faults; not native DB evidence."""
import json
from uuid import uuid4

from psycopg.pq import TransactionStatus
import pytest

from mfdataindia.nse import cli, store
from nse01_fixtures import FIXTURES, WALK, candidate


class Connection:
    info=type('Info',(),{'transaction_status':TransactionStatus.IDLE})()
    def __init__(self, fault=None):
        self.fault=fault
        self.commits=0
        self.rollbacks=0
        self.queries=[]
    def execute(self,query,params=None):
        self.queries.append(query)
        if self.fault=='readback' and query.startswith('SELECT snapshot_id'):
            raise OSError('synthetic-secret-marker')
        return self
    def fetchone(self):
        return {'snapshot_id':'00000000-0000-0000-0000-000000000001','disposition':'PUBLISHED_WITH_LIMITATIONS'}
    def commit(self):
        self.commits+=1
        if self.fault=='commit':
            raise OSError('synthetic-secret-marker')
    def rollback(self):
        self.rollbacks+=1
        if self.fault=='rollback':
            raise OSError('synthetic-secret-marker')
    def close(self):
        if self.fault=='close':
            raise OSError('synthetic-secret-marker')


def staged(*args):
    return {'snapshot_id':'00000000-0000-0000-0000-000000000001','state':'PUBLISHED_WITH_LIMITATIONS'}


@pytest.mark.parametrize('fault,effect,state,commits',[
    ('commit','UNKNOWN','COMMIT_UNKNOWN',1),
    ('readback','COMMITTED_UNVERIFIED','COMMITTED_UNVERIFIED',1),
    ('rollback','UNKNOWN','ROLLBACK_UNCONFIRMED',0),
    ('before','ROLLBACK_CONFIRMED','PUBLICATION_ROLLED_BACK',0),
])
def test_commit_rollback_ambiguity_dominates_and_no_retry(monkeypatch,fault,effect,state,commits):
    conn=Connection(fault)
    def fail(*args):
        raise OSError('synthetic-secret-marker')
    monkeypatch.setattr(store,'_stage',fail if fault in {'before','rollback'} else staged)
    with pytest.raises(store.OutcomeError) as error:
        store.publish(conn,candidate(),WALK,str(uuid4()),expected_head=None)
    report=error.value.report
    assert report['effect']==effect and report['state']==state
    assert conn.commits==commits
    assert conn.rollbacks==(1 if commits==0 else 0)
    assert 'synthetic-secret-marker' not in json.dumps(report)


def arguments(tmp_path,op):
    return ['--import-file','--file',str(FIXTURES/'sch43_full.psv'),
            '--manifest',str(FIXTURES/'sch43_full.manifest.json'),
            '--crosswalk',str(FIXTURES/'crosswalk.json'),'--operation',op,'--journal-dir',str(tmp_path)]


def test_durable_intent_failure_prevents_connection_and_preserves_incomplete_history(tmp_path,monkeypatch):
    calls=[]
    op=str(uuid4())
    def fail(*a):
        raise OSError('synthetic-secret-marker')
    monkeypatch.setattr(cli.os,'fsync',fail)
    output=[]
    assert cli.main(arguments(tmp_path,op),connect=lambda *a,**kw:calls.append(1),output=output.append)==1
    assert calls==[] and (tmp_path/(op+'.json')).exists()
    assert json.loads(output[-1])['effect']=='NO_PUBLICATION'
    monkeypatch.undo()
    assert cli.main(arguments(tmp_path,op),connect=lambda *a,**kw:calls.append(1),output=output.append)==1
    assert calls==[] and json.loads(output[-1])['reason']=='OPERATION_SPENT'
    assert 'synthetic-secret-marker' not in ''.join(output)


def test_cleanup_failure_retains_committed_effect_and_no_second_submission(tmp_path,monkeypatch):
    conn=Connection('close')
    monkeypatch.setattr(store,'_stage',staged)
    output=[]
    args=arguments(tmp_path,str(uuid4()))
    code=cli.main(args,environ={'MFDATAINDIA_DSN':'synthetic-secret-marker'},connect=lambda *a,**kw:conn,output=output.append)
    assert code==1 and conn.commits==1
    report=json.loads(output[-1])
    assert report['effect']=='COMMITTED_VERIFIED' and report['reason']=='CONNECTION_CLEANUP_FAILED'
    assert cli.main(args,connect=lambda *a,**kw:pytest.fail('reconnected'),output=output.append)==1
    assert conn.commits==1 and 'synthetic-secret-marker' not in ''.join(output)


def test_context_free_execution_or_inspection_failure_is_unknown(tmp_path,monkeypatch):
    conn=Connection()
    def fail(*a,**kw):
        raise OSError('synthetic-secret-marker')
    monkeypatch.setattr(store,'publish',fail)
    monkeypatch.setattr(store,'inspect',fail)
    for args in (arguments(tmp_path,str(uuid4())),['--inspect','--operation',str(uuid4())]):
        output=[]
        assert cli.main(args,environ={'MFDATAINDIA_DSN':'synthetic-secret-marker'},connect=lambda *a,**kw:conn,output=output.append)==1
        report=json.loads(output[-1])
        assert report['state']==report['effect']=='UNKNOWN'
        assert report['disposition']=='INSPECT_ONLY' and 'synthetic-secret-marker' not in output[-1]
    assert conn.commits==conn.rollbacks==0


def test_stderr_failure_exits_unsuccessfully_without_retry(tmp_path,monkeypatch):
    conn=Connection()
    monkeypatch.setattr(store,'_stage',staged)
    def unavailable(*args):
        raise OSError('synthetic-secret-marker')
    assert cli.main(arguments(tmp_path,str(uuid4())),environ={'MFDATAINDIA_DSN':'fixture'},connect=lambda *a,**kw:conn,output=unavailable)==1
    assert conn.commits==1
