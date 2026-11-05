"""Explicit current multi-source method transition; no ingestion or grants."""
import hashlib
from pathlib import Path
from psycopg.types.json import Jsonb
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
    with conn.transaction():
        monthly_projection.owner(conn)
        multi_store.verify_acl(conn,principal='mfdata_verified_reader',reader_profile=profiles.BROAD)
        book_store.verify_acl(conn,principal='mfdata_verified_reader',reader_profile=profiles.BROAD)
        current.verify_acl(conn)
        registry,policy=installed_policy(conn)
        cfg=book_store.installed_configuration(conn)
        prior=dict(current.configuration(conn))
        if registry==hdfc_source_v2.registry() and cfg['document']==book_sources.configuration(8):
            if prior['policy_hash']!=fingerprint(book_sources.current_policy(8)):
                raise ValueError('HDFC_INSTALLATION_INCOMPLETE')
            return {'status':'ALREADY_INSTALLED','business_rows_changed':0}
        multi=conn.execute('SELECT * FROM mf_multi_facts.configuration ORDER BY generation DESC LIMIT 1').fetchone()
        if (registry!=multi_facts.registry() or fingerprint(dict(multi))!=expected_multi_hash
                or cfg['configuration_hash']!=expected_book_hash or cfg['document']!=book_sources.configuration(7)
                or fingerprint(prior)!=expected_current_hash or prior['policy_hash']!=fingerprint(book_sources.current_policy(7))
                or not current.projection_state(conn)):
            raise ValueError('HDFC_INSTALLATION_BASE_DIFFERS')
        before=multi_store.capture(conn,reader_profile=profiles.BROAD)
        for key,value in cfg['document']['source_specifications'].items():
            if value!=book_sources.configuration(8)['source_specifications'][key]:
                raise ValueError('HDFC_UNRELATED_SOURCE_CHANGED')
        registry,policy=hdfc_source_v2.registry(),hdfc_source_v2.policy()
        transition={'format':'hdfc-dynamic-source-transition-v2','prior_multi_configuration':dict(multi),
            'target_registry_hash':fingerprint(registry),'target_policy_hash':fingerprint(policy),
            'prior_book_configuration_hash':cfg['configuration_hash'],'business_rows_changed':0}
        transition['transition_id']=fingerprint(transition)
        multi_store.append_exact(conn,'transitions','transition_id',transition,{})
        conn.execute('INSERT INTO mf_multi_facts.configuration VALUES(%s,%s,%s,%s)',
            (multi['generation']+1,fingerprint(registry),Jsonb(registry),fingerprint(policy)))
        conn.execute(sealer(root))
        newcfg=book_sources.configuration(8)
        conn.execute('INSERT INTO mf_book_facts.configuration VALUES(%s,%s,%s)',
            (cfg['generation']+1,fingerprint(newcfg),Jsonb(newcfg)))
        current_policy=book_sources.current_policy(8)
        for document in (policy,current_policy):
            review._insert_exact(conn,'policies','policy_hash',fingerprint(document),document,
                {'policy_id':document['id'],'policy_version':document['version']})
        monthly_projection.seal_configuration(conn,prior['generation']+1,prior['baseline_definition'],
            fingerprint(current_policy),prior['view_definitions'])
        multi_store.verify_acl(conn,principal='mfdata_verified_reader',reader_profile=profiles.BROAD)
        book_store.verify_acl(conn,principal='mfdata_verified_reader',reader_profile=profiles.BROAD)
        current.verify_acl(conn)
        after=multi_store.capture(conn,reader_profile=profiles.BROAD)
        for name in ('sources','snapshots','runs','original_byte_integrity_checked'):
            if before[name]!=after[name]:
                raise ValueError('HDFC_BUSINESS_PRESERVATION_DIFFERS')
        for table in ('configuration','transitions'):
            if not before[table].items()<=after[table].items():
                raise ValueError('HDFC_HISTORY_PRESERVATION_DIFFERS')
        return {'status':'INSTALLED','transition_id':transition['transition_id'],'business_rows_changed':0}
