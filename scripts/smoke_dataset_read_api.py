"""Actual packaged ASGI factory/routes with synthetic SQL transport, no network.

Database semantics and Cloud Run authentication are NOT claimed by this smoke;
native PostgreSQL and the deployed TI path supply those independent proofs.
"""
import asyncio
from contextlib import nullcontext
import json

from mfdataindia.datasets.app import create_app
from mfdataindia.review import broad_reader_policy


def empty_profile():
    null = {'is_null':True,'sha256':None,'utf8_bytes':0}
    flags = {k:False for k in ('rolsuper','rolinherit','rolcreaterole','rolcreatedb','rolreplication','rolbypassrls')}
    return {'relations':[],'columns':[],'functions':[],'memberships':[],'policies':[],
        'roles':[{'oid':oid,'rolname':name,'rolcanlogin':True,'rolconfig':null,**flags}
                 for oid,name in ((100,'mfdata_admin'),(101,'mfdata_verified_reader'))],
        'namespaces':[{'oid':10,'nspname':'mf','owner':100,'acl':[[100,101,'USAGE',False]],'usage':True,'create_allowed':False}],
        'databases':[{'oid':20,'datname':'mfdataindia','owner':100,'acl':[[100,101,'CONNECT',False]],
                      'connect_allowed':True,'create_allowed':False,'temporary_allowed':False}],
        'defaults':[{'oid':30,'defaclrole':100,'defaclnamespace':10,'defaclobjtype':'r','acl':[[100,101,'SELECT',False]]}]}


class Cursor:
    def __init__(self, profile=False):
        self.profile = profile

    def fetchall(self):
        return []

    def fetchone(self):
        if self.profile:
            return {'value':empty_profile()}
        return {'database':'mfdataindia','principal':'mfdata_verified_reader',
                'session':'mfdata_verified_reader','readonly':'on','isolation':'repeatable read',
                **{k:False for k in ('rolsuper','rolinherit','rolcreaterole','rolcreatedb',
                    'rolreplication','rolbypassrls','membership','database_create')}}


class SyntheticConnection:
    def transaction(self):
        return nullcontext()

    def execute(self, query, parameters=None):
        text = str(query)
        # Privilege inquiries legitimately contain strings such as 'GRANT
        # OPTION'. Inspect the statement verb, not words inside SQL literals.
        assert text.lstrip().split(None,1)[0].upper() in {'SELECT','SET','WITH'}
        return Cursor(query==broad_reader_policy.STATE_SQL)


class SyntheticPool:
    def connection(self):
        return nullcontext(SyntheticConnection())


async def request(app, path, *, method='GET', query=b''):
    messages = []
    sent = False
    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {'type':'http.request','body':b'','more_body':False}
        await asyncio.sleep(3600)
    async def send(message):
        messages.append(message)
    await app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1',
               'method':method,'path':path,'raw_path':path.encode(),'query_string':query,
               'headers':[],'client':('127.0.0.1',1),'server':('fixture',80),
               'scheme':'http','root_path':''}, receive, send)
    status = next(m['status'] for m in messages if m['type']=='http.response.start')
    body = b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body')
    return status, body


async def smoke():
    app = create_app('postgresql://fixture:fixture@127.0.0.1:1/fixture')
    app.state.db_pool = SyntheticPool()
    status, body = await request(app,'/api/datasets/v1')
    assert status == 200 and json.loads(body) == {'format':'mfdata-consumer-datasets-v1','datasets':[]}
    status, _ = await request(app,'/api/datasets/v1/d_' + '1'*32 + '/rows',query=b'limit=101')
    assert status == 422
    status, _ = await request(app,'/api/datasets/v1',method='POST')
    assert status == 405
    return {'packaged_dataset_ASGI_entrypoint':'PASS','provider_requests':0,
            'database_connections':0,'writes':0,'native_DB_or_Cloud_Run_auth_proof':False}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(smoke()),sort_keys=True))
