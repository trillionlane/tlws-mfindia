from contextlib import nullcontext

import pytest

from mfdataindia.datasets import policy as p, projections
from mfdataindia.review.policy import fingerprint


class Cursor:
    def __init__(self,rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self,rows):
        self.rows = rows

    def transaction(self):
        return nullcontext()

    def execute(self,query,parameters):
        assert query.lstrip().startswith('WITH named_views')
        assert set(parameters[0])==projections.NAMES
        return Cursor(self.rows)


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
