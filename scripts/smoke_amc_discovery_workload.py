"""Actual packaged CLI and shared worker cycle with synthetic transports only."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

from mfdataindia.enrichment import (ingestion_workload as workload,
    ingestion_runner as runner, ingestion_discovery as discovery, ingestion_gate as gate)


def smoke():
    root = Path(__file__).resolve().parents[1]
    workload.allocation(root)
    spec = importlib.util.spec_from_file_location('packaged_current_amc_main',root/'scripts/refresh_registered_amc_gate_dev.py')
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)
    env = {k:v for k,v in os.environ.items() if not k.startswith(('PG','CLOUD_RUN_')) and k!='MFDATAINDIA_DSN'}
    env.update(MFDATAINDIA_DSN='host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev dbname=mfdataindia user=mfdata_verified_generator password=synthetic-only',
               DEPLOYMENT_GIT_SHA='a'*40,CLOUD_RUN_JOB='tlws-mf-official-facts-refresh-dev',
               CLOUD_RUN_EXECUTION='tlws-mf-official-facts-refresh-dev-synthetic-gate',
               CLOUD_RUN_TASK_COUNT='6',CLOUD_RUN_TASK_INDEX='5',CLOUD_RUN_TASK_ATTEMPT='0')
    calls=[]
    class Adapter:
        def __init__(self,*args,**kwargs):
            pass
        def discover(self,deadline):
            deadline()
            return discovery.Discovery('uti-expanded',discovery.State.READY), runner.Candidate(
                [{'role':'synthetic','content':b'%PDF-synthetic-stage'}],'a'*64,'b'*64)
        def prepare(self,candidate,deadline):
            deadline()
            return runner.Prepared('2026-10-31',lambda conn:None)
    class Store:
        def __init__(self,*args,**kwargs):
            pass
        def latest_published(self,source):
            return None
        def due(self,source):
            return True
        def published_input(self,*args):
            return None
        def claim(self,candidate,execution,**kwargs):
            return gate.Claim(discovery.State.CLAIMED,{'claim_id':'c'*64,'execution_id':execution},candidate)
        def publish(self,claimed,publisher,progress,deadline):
            calls.append('synthetic-publication')
            progress.effect='PUBLICATION_VERIFIED'
        def poll(self,observation):
            calls.append('synthetic-poll')
    with patch.dict(os.environ,env,clear=True), patch.object(workload,'verify_database'), \
            patch.object(workload.ingestion_adapters,'Adapter',Adapter), \
            patch.object(workload.ingestion_postgres,'Store',Store):
        output=io.StringIO()
        with redirect_stdout(output):
            main.main([],source_root=root)
    result=json.loads(output.getvalue())
    assert result['status']=='completed' and result['discovery']['state']=='PUBLISHED'
    assert result['discovery']['publication_effect']=='PUBLICATION_VERIFIED'
    assert calls==['synthetic-publication','synthetic-poll']
    return {'packaged_amc_discovery_cycle':'PASS','synthetic_transports':True,
            'actual_provider_requests':0,'actual_database_connections':0,'live_acceptance':False}


if __name__=='__main__':
    print(json.dumps(smoke(),sort_keys=True))
