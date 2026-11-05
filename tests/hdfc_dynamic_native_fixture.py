"""Current HDFC method after the genuine v4 -> layout6 -> shared7 installers."""
from mfdataindia.enrichment import hdfc_install,book_store,current_facts_store as current
from mfdataindia.review.policy import fingerprint
from test_ro01_reader_policy_postgres import connect


def install_hdfc(dsn,root):
    with connect(dsn,autocommit=False) as conn:
        multi=dict(conn.execute('SELECT * FROM mf_multi_facts.configuration ORDER BY generation DESC LIMIT 1').fetchone())
        book=book_store.installed_configuration(conn)
        prior=dict(current.configuration(conn))
        pins={'expected_multi_hash':fingerprint(multi),'expected_book_hash':book['configuration_hash'],
              'expected_current_hash':fingerprint(prior)}
        result=hdfc_install.install(conn,root,**pins)
        assert result['status']=='INSTALLED'
    with connect(dsn,autocommit=False) as conn:
        assert hdfc_install.install(conn,root,**pins)['status']=='ALREADY_INSTALLED'
    return result
