"""Provider-free streaming/control comparison; native composition is separate."""
from contextlib import contextmanager
import hashlib
import json
import pytest
from mfdataindia.enrichment import ingestion_install as install
from mfdataindia.review.preservation import compare_preservation,compare_preservation_structure


class Result:
    def __init__(self,value):
        self.value=value
    def fetchone(self):
        return self.value
    def fetchall(self):
        return self.value


class Connection:
    copies=0
    def execute(self,query):
        if query=='SHOW transaction_isolation':
            return Result({'transaction_isolation':'repeatable read'})
        return Result([{'relname':name,'relkind':'r'} for name in ('claims','events','polls','work')])
    @contextmanager
    def cursor(self):
        connection=self
        class Cursor:
            @contextmanager
            def copy(self,query):
                text=query.as_string(None)
                connection.copies+=1
                class Copy:
                    def set_types(self,types):
                        assert types==['text']
                    def rows(self):
                        return iter([('{"synthetic":"SECRET_NEVER_EMIT_é"}',)]) if '"work"' in text else iter([])
                yield Copy()
        yield Cursor()


def test_private_control_capture_streams_every_table_and_exposes_hashes_only(monkeypatch):
    monkeypatch.setattr(install,'verify',lambda conn:{'catalogue_hash':'a'*64})
    conn=Connection()
    actual=install.capture(conn)
    value='{"synthetic":"SECRET_NEVER_EMIT_é"}'.encode()
    assert actual['tables']['work']=={'rows':1,'sha256':hashlib.sha256(len(value).to_bytes(8,'big')+value).hexdigest()}
    assert set(actual['tables'])=={'claims','events','polls','work'}
    assert all(row['rows']==0 for key,row in actual['tables'].items() if key!='work')
    assert 'SECRET_NEVER_EMIT' not in json.dumps(actual) and conn.copies==4
    conn=Connection()
    assert install.capture(conn,structure_only=True)['tables']=={} and conn.copies==0


@pytest.mark.parametrize('structure_only',[False,True])
def test_core_comparison_binds_control_structure_without_forbidding_operational_append(structure_only):
    before={'format':'mfdata-preservation-structure-v1' if structure_only else 'mfdata-preservation-v1',
        'tables':{} if structure_only else {'funds':{'rows':1,'sha256':'a'*64}},'optional_migration':None,
        **{k:'a'*64 for k in ('structure_hash','functions_hash','triggers_hash','privileges_hash','sequences_hash','ownership_hash')},
        'ingestion_operations':{'format':'amc-ingestion-control-preservation-v1','catalogue_hash':'a'*64}}
    compare=(lambda a,b:compare_preservation_structure(a,b)) if structure_only else (lambda a,b:compare_preservation(a,b,'b'*64))
    compare(before,dict(before))
    after={**before,'ingestion_operations':{**before['ingestion_operations'],'catalogue_hash':'c'*64}}
    with pytest.raises(ValueError,match='ingestion control structure changed'):
        compare(before,after)
