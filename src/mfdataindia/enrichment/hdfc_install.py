"""Explicit current multi-source method transition; no ingestion or grants."""
import hashlib
from pathlib import Path
from psycopg.types.json import Jsonb
from psycopg.pq import TransactionStatus
from mfdataindia.enrichment import (multi_facts,multi_store,hdfc_source_v2,
    book_sources,book_store,current_facts_store as current,monthly_projection)
from mfdataindia.review import reader_access_profile as profiles,store as review
from mfdataindia.review.policy import fingerprint

FILE='sql/operations/hdfc_dynamic_seal_v2.sql'
SHA256='458f97edc6bfeb6758f4f2dded5bc03c2191c70b9f58ee214e29645d103330cb'


def sealer(root=None):
    root=Path(root) if root else (Path('/app') if (Path('/app')/FILE).is_file() else Path(__file__).resolve().parents[3])
    raw=(root/FILE).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SHA256:
        raise ValueError('HDFC_DYNAMIC_SEAL_SOURCE_DIFFERS')
    return raw.decode()


def installed_policy(conn):
    row=conn.execute('SELECT registry_hash,registry,policy_hash FROM mf_multi_facts.configuration ORDER BY generation DESC LIMIT 1').fetchone()
    registry,policy=multi_facts.registry(),multi_facts.policy()
    if row=={'registry_hash':fingerprint(registry),'registry':registry,'policy_hash':fingerprint(policy)}:
        return registry,policy
    registry,policy=hdfc_source_v2.registry(),hdfc_source_v2.policy()
    if row=={'registry_hash':fingerprint(registry),'registry':registry,'policy_hash':fingerprint(policy)}:
        return registry,policy
    raise ValueError('HDFC_INSTALLED_SOURCE_AUTHORITY_DIFFERS')


def verify_seal(conn):
    raw=sealer()
    expected=raw.split('AS $$',1)[1].split('END $$;',1)[0]+'END '
    row=conn.execute("SELECT prosrc FROM pg_proc WHERE oid='mf_multi_facts.seal_input()'::regprocedure").fetchone()
    if row is None or row['prosrc']!=expected:
        raise ValueError('HDFC_INSTALLED_SEAL_DIFFERS')


def install(conn,root,*,expected_multi_hash,expected_book_hash,expected_current_hash):
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('HDFC_INSTALL_REQUIRES_IDLE_CONNECTION')
    with book_sources.reuse_current_policies():
        return _install(conn,root,expected_multi_hash=expected_multi_hash,
            expected_book_hash=expected_book_hash,expected_current_hash=expected_current_hash)


def _install(conn,root,*,expected_multi_hash,expected_book_hash,expected_current_hash):
    # Immutable source work is outside the short owner transaction. No database
    # verdict is cached: actual identities/state/permissions are checked below.
    old_registry,old_policy=multi_facts.registry(),multi_facts.policy()
    registry,policy=hdfc_source_v2.registry(),hdfc_source_v2.policy()
    old_book,newcfg=book_sources.configuration(7),book_sources.configuration(8)
    old_current,current_policy=book_sources.current_policy(7),book_sources.current_policy(8)
    old_policy_hash,new_policy_hash=fingerprint(old_current),fingerprint(current_policy)
    legacy={'registry_hash':fingerprint(old_registry),'registry':old_registry,'policy_hash':fingerprint(old_policy)}
    target={'registry_hash':fingerprint(registry),'registry':registry,'policy_hash':fingerprint(policy)}
    sql_source=sealer(root)
    if old_book['source_specifications']!=newcfg['source_specifications']:
        raise ValueError('HDFC_UNRELATED_SOURCE_CHANGED')
    with conn.transaction():
        monthly_projection.owner(conn)
        book_store.verify_acl(conn,principal='mfdata_verified_reader',reader_profile=profiles.BROAD)
        current.verify_acl(conn)
        cfg=book_store.installed_configuration(conn)
        prior=dict(current.configuration(conn))
        multi=conn.execute('SELECT * FROM mf_multi_facts.configuration ORDER BY generation DESC LIMIT 1').fetchone()
        if multi is None:
            raise ValueError('HDFC_INSTALLATION_BASE_DIFFERS')
        installed={key:multi[key] for key in ('registry_hash','registry','policy_hash')}
        if installed==target and cfg['document']==newcfg:
            multi_store.verify_acl(conn,principal='mfdata_verified_reader',reader_profile=profiles.BROAD)
            if prior['policy_hash']!=new_policy_hash:
                raise ValueError('HDFC_INSTALLATION_INCOMPLETE')
            return {'status':'ALREADY_INSTALLED','business_rows_changed':0}
        if (installed!=legacy or fingerprint(dict(multi))!=expected_multi_hash
                or cfg['configuration_hash']!=expected_book_hash or cfg['document']!=old_book
                or fingerprint(prior)!=expected_current_hash or prior['policy_hash']!=old_policy_hash
                or not current.projection_state(conn)):
            raise ValueError('HDFC_INSTALLATION_BASE_DIFFERS')
        # capture() includes the complete multi ACL/policy/original-integrity
        # guard, once per phase, before and after the actual transition.
        before=multi_store.capture(conn,reader_profile=profiles.BROAD)
        transition={'format':'hdfc-dynamic-source-transition-v2','prior_multi_configuration':dict(multi),
            'target_registry_hash':target['registry_hash'],'target_policy_hash':target['policy_hash'],
            'prior_book_configuration_hash':cfg['configuration_hash'],'business_rows_changed':0}
        transition['transition_id']=fingerprint(transition)
        multi_store.append_exact(conn,'transitions','transition_id',transition,{})
        conn.execute('INSERT INTO mf_multi_facts.configuration VALUES(%s,%s,%s,%s)',
            (multi['generation']+1,target['registry_hash'],Jsonb(registry),target['policy_hash']))
        conn.execute(sql_source)
        conn.execute('INSERT INTO mf_book_facts.configuration VALUES(%s,%s,%s)',
            (cfg['generation']+1,fingerprint(newcfg),Jsonb(newcfg)))
        for document in (policy,current_policy):
            review._insert_exact(conn,'policies','policy_hash',fingerprint(document),document,
                {'policy_id':document['id'],'policy_version':document['version']})
        monthly_projection.seal_configuration(conn,prior['generation']+1,prior['baseline_definition'],
            new_policy_hash,prior['view_definitions'])
        after=multi_store.capture(conn,reader_profile=profiles.BROAD)
        book_store.verify_acl(conn,principal='mfdata_verified_reader',reader_profile=profiles.BROAD)
        current.verify_acl(conn)
        for name in ('sources','snapshots','runs','original_byte_integrity_checked'):
            if before[name]!=after[name]:
                raise ValueError('HDFC_BUSINESS_PRESERVATION_DIFFERS')
        for table in ('configuration','transitions'):
            if not before[table].items()<=after[table].items():
                raise ValueError('HDFC_HISTORY_PRESERVATION_DIFFERS')
        return {'status':'INSTALLED','transition_id':transition['transition_id'],'business_rows_changed':0}
