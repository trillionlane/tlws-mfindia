"""Current HDFC method, measured with real SQL/COMMIT and unchanged owner bounds."""
from contextlib import contextmanager
import json
import time
import os

from mfdataindia.enrichment import hdfc_install,book_store,current_facts_store as current
from mfdataindia.review import broad_reader_policy
from mfdataindia.review.policy import fingerprint
from test_ro01_reader_policy_postgres import connect


class Measured:
    """Forward every operation/result unchanged; retain only counts and timings."""
    def __init__(self,conn):
        self.conn=conn
        self.stats={'catalogue_queries':0,'catalogue_seconds':0.,'sql_calls':0,
                    'slowest_query_seconds':0.,'transaction_scopes':0,'configured_limits':{}}
        self.locked_at=None

    def __getattr__(self,key):
        return getattr(self.conn,key)

    def execute(self,query,*args,**kwargs):
        started=time.monotonic()
        try:
            return self.conn.execute(query,*args,**kwargs)
        finally:
            seconds=time.monotonic()-started
            self.stats['sql_calls']+=1
            self.stats['slowest_query_seconds']=max(self.stats['slowest_query_seconds'],seconds)
            if isinstance(query,str):
                if query==broad_reader_policy.STATE_SQL:
                    self.stats['catalogue_queries']+=1
                    self.stats['catalogue_seconds']+=seconds
                if query.startswith('SELECT pg_try_advisory_xact_lock('):
                    self.locked_at=time.monotonic()
                for name,value in (('statement_timeout','5s'),('lock_timeout','5s'),('transaction_timeout','15s')):
                    if query==f"SET LOCAL {name}='{value}'":
                        self.stats['configured_limits'][name]=value

    @contextmanager
    def transaction(self):
        started=time.monotonic()
        cpu_started=time.process_time()
        self.stats['transaction_scopes']+=1
        try:
            with self.conn.transaction() as txn:
                yield txn
        finally:
            ended=time.monotonic()
            self.stats['transaction_seconds']=ended-started
            self.stats['transaction_cpu_seconds']=time.process_time()-cpu_started
            self.stats['lock_hold_seconds']=ended-self.locked_at if self.locked_at is not None else None


def install_hdfc(dsn,root):
    # Full installer/re-entry on one available hosted CPU, restored on all exits.
    # This is not an emulator for DEV latency or a provider/publication proof.
    prior=os.sched_getaffinity(0)
    os.sched_setaffinity(0,{min(prior)})
    try:
        return _install_hdfc(dsn,root)
    finally:
        os.sched_setaffinity(0,prior)


def _install_hdfc(dsn,root):
    # Input observations do not age the subsequent short owner transaction.
    # The installer rechecks every expected value after its normal lock begins.
    with connect(dsn) as conn:
        multi=dict(conn.execute('SELECT * FROM mf_multi_facts.configuration ORDER BY generation DESC LIMIT 1').fetchone())
        book=book_store.installed_configuration(conn)
        prior=dict(current.configuration(conn))
        pins={'expected_multi_hash':fingerprint(multi),'expected_book_hash':book['configuration_hash'],
              'expected_current_hash':fingerprint(prior)}
        observed=Measured(conn)
        try:
            started=time.monotonic()
            result=hdfc_install.install(observed,root,**pins)
            observed.stats['complete_installer_seconds']=time.monotonic()-started
        except Exception as error:
            print(json.dumps({'native_hdfc_owner_install':'FAILED','type':type(error).__name__,**observed.stats},sort_keys=True))
            raise
        assert result['status']=='INSTALLED'
        assert observed.stats['catalogue_queries']==4
        assert observed.stats['transaction_scopes']==1
        assert observed.stats['configured_limits']=={'statement_timeout':'5s','lock_timeout':'5s','transaction_timeout':'15s'}
        assert observed.stats['transaction_seconds']<15
    with connect(dsn) as conn:
        reentry=Measured(conn)
        started=time.monotonic()
        assert hdfc_install.install(reentry,root,**pins)['status']=='ALREADY_INSTALLED'
        reentry.stats['complete_installer_seconds']=time.monotonic()-started
        assert reentry.stats['catalogue_queries']==2
        assert reentry.stats['transaction_scopes']==1
        assert reentry.stats['configured_limits']==observed.stats['configured_limits']
        assert reentry.stats['transaction_seconds']<15
    return {**result,'native_install_observation':observed.stats,'native_reentry_observation':reentry.stats}
