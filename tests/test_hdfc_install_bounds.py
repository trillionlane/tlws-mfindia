"""Duplicate guard and immutable-preparation regressions, not native SQL proof."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from psycopg.pq import TransactionStatus
from mfdataindia.enrichment import book_store,multi_store,hdfc_install,multi_facts
from mfdataindia.review import privileges,reader_access_profile as profiles


@pytest.mark.parametrize('module',[book_store,multi_store])
def test_store_selection_is_checked_once_by_real_principal_boundary(monkeypatch,module):
    calls=[]
    class ReachedArtifactBoundary(Exception):
        pass
    monkeypatch.setattr(profiles,'verify_selection',lambda *a,**k:calls.append((a,k)))
    def stop(*a,**k):raise ReachedArtifactBoundary()
    monkeypatch.setattr(privileges,'verify_artifact_acl',stop)
    with pytest.raises(ReachedArtifactBoundary):
        module.verify_acl(object(),principal='mfdata_verified_reader',reader_profile=profiles.BROAD)
    assert len(calls)==1
    assert calls[0][0][1:]==(profiles.BROAD,'mfdata_verified_reader')


def test_immutable_source_failure_occurs_before_opening_owner_transaction(monkeypatch):
    class ImmutableBoundary(Exception):
        pass
    class Conn:
        info=SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        active=False
        @contextmanager
        def transaction(self):
            self.active=True
            try:
                yield
            finally:
                self.active=False
        def execute(self,*a,**k):pytest.fail('source preparation reached database')
    conn=Conn()
    monkeypatch.setattr(hdfc_install.monthly_projection,'owner',lambda *a:None)
    monkeypatch.setattr(multi_store,'verify_acl',lambda *a,**k:None)
    monkeypatch.setattr(book_store,'verify_acl',lambda *a,**k:None)
    monkeypatch.setattr(hdfc_install.current,'verify_acl',lambda *a,**k:None)
    def prepare(*args,**kwargs):
        assert conn.active is False
        raise ImmutableBoundary()
    monkeypatch.setattr(multi_facts,'registry',prepare)
    monkeypatch.setattr(hdfc_install,'installed_policy',lambda c:(multi_facts.registry(),{}))
    with pytest.raises(ImmutableBoundary):
        hdfc_install.install(conn,None,expected_multi_hash='a',expected_book_hash='b',expected_current_hash='c')


def test_active_transaction_is_rejected_before_source_or_database_work(monkeypatch):
    conn=SimpleNamespace(info=SimpleNamespace(transaction_status=TransactionStatus.INTRANS))
    monkeypatch.setattr(multi_facts,'registry',lambda *a,**k:pytest.fail('source work before idle rejection'))
    with pytest.raises(ValueError,match='^HDFC_INSTALL_REQUIRES_IDLE_CONNECTION$'):
        hdfc_install.install(conn,None,expected_multi_hash='a',expected_book_hash='b',expected_current_hash='c')


@pytest.mark.parametrize('module',[book_store,multi_store])
def test_unknown_profile_still_rejects_before_database_access(module):
    with pytest.raises(profiles.ReaderAccessError,match='^INVALID_READER_ACCESS_PROFILE$'):
        module.verify_acl(object(),principal='mfdata_verified_reader',reader_profile='untrusted-profile')
