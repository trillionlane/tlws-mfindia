from contextlib import contextmanager

import pytest
import psycopg

from mfdataindia.datasets import policy as p, projections
from mfdataindia.review.policy import fingerprint


class Cursor:
    def __init__(self,rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self,rows,search_path='pg_catalog, public'):
        self.rows = rows
        self.search_path = search_path
        self.observed_paths = []

    @contextmanager
    def transaction(self, *, force_rollback=False):
        before = self.search_path
        try:
            yield
        finally:
            if force_rollback:
                self.search_path = before

    def execute(self,query,parameters=None):
        if query == "SELECT pg_catalog.set_config('search_path','pg_catalog, public',true)":
            self.search_path = 'pg_catalog, public'
            return Cursor([])
        if query.lstrip().startswith('SELECT DISTINCT ON(policy_hash)'):
            return Cursor([])
        assert query.lstrip().startswith('WITH named_views')
        assert set(parameters[0])==projections.NAMES
        self.observed_paths.append(self.search_path)
        visible = 'mf' in {part.strip() for part in self.search_path.split(',')}
        # Model PostgreSQL's real namespace-sensitive deparser: a visible mf
        # table is rendered without its qualification, changing the exact seal.
        return Cursor([{**row,'definition':row['definition'].replace('mf.funds','funds')}
                       if visible else row for row in self.rows])


def test_core_only_installation_cannot_invent_installed_projection_seals():
    assert projections.approved_oids(Connection([]))==[]


@pytest.mark.parametrize('mutation',['definition','owner','options','missing'])
def test_a_named_view_without_its_exact_installed_seal_is_not_an_exception(monkeypatch,mutation):
    rows = [{'name':'mf_live_facts.'+name,'oid':index+1,'kind':'v','owner':p.OWNER,
             'schema_owner':p.OWNER,'options':None,'definition':'SELECT 1 AS '+name}
            for index,name in enumerate(sorted(projections.current.VIEWS))]
    cfg = {'baseline_definition':next(r['definition'] for r in rows if r['name'].endswith('.baseline')),
           'view_hashes':{r['name'].split('.')[1]:fingerprint(r['definition']) for r in rows}}
    monkeypatch.setattr(projections.current,'configuration',lambda conn:cfg)
    if mutation=='definition':
        rows[0]['definition']+=' WHERE false'
    elif mutation=='owner':
        rows[0]['owner']=p.READER
    elif mutation=='options':
        rows[0]['options']=['security_invoker=true']
    else:
        rows.pop()
    assert projections.approved_oids(Connection(rows))==[]


@pytest.mark.parametrize('path',['mf, public','custom_schema, mf, public','public','pg_catalog, public'])
@pytest.mark.parametrize('invalid',[False,True])
def test_projection_seals_use_installation_context_and_restore_caller_path(monkeypatch,path,invalid):
    rows = [{'name':'mf_live_facts.'+name,'oid':index+1,'kind':'v','owner':p.OWNER,
             'schema_owner':p.OWNER,'options':None,
             'definition':'SELECT amfi_scheme_code FROM mf.funds -- '+name}
            for index,name in enumerate(sorted(projections.current.VIEWS))]
    cfg = {'baseline_definition':next(r['definition'] for r in rows if r['name'].endswith('.baseline')),
           'view_hashes':{r['name'].split('.')[1]:fingerprint(r['definition']) for r in rows}}
    monkeypatch.setattr(projections.current,'configuration',lambda conn:cfg)
    if invalid:
        rows[0]['definition']+=' WHERE false'
    conn = Connection(rows,search_path=path)
    expected = [] if invalid else list(range(1,len(rows)+1))
    assert projections.approved_oids(conn)==expected
    assert conn.observed_paths==['pg_catalog, public']
    assert conn.search_path==path


def test_failed_metadata_query_restores_caller_context_without_exposing_error_text():
    class BrokenConnection(Connection):
        def execute(self,query,parameters=None):
            if query.lstrip().startswith('WITH named_views'):
                self.observed_paths.append(self.search_path)
                raise psycopg.ProgrammingError('synthetic-secret-marker')
            return super().execute(query,parameters)
    conn = BrokenConnection([],search_path='mf, public')
    assert projections.approved_oids(conn)==[]
    assert conn.observed_paths==['pg_catalog, public']
    assert conn.search_path=='mf, public'
