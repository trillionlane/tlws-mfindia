"""Current fixed HDFC publisher; preparation is separate from the shared claim.

No acquisition occurs inside the business transaction. The two immutable PDF
archives, actual identity, typed fields, canonical seals and receipt stay in
that existing transaction, followed by the shared completion before COMMIT.
"""
import hashlib
from mfdataindia.enrichment import (hdfc_monthly_v2 as method,hdfc_source_v2 as authority,
    multi_store,multi_runtime,store as evidence)
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.store.locks import acquire_nav_data_operation_lock


def prepare(identity,book,summary,*,book_url,listing,observed,extractor=None):
    options={} if extractor is None else {'extractor':extractor}
    return method.derive(identity,book,summary,book_url=book_url,listing=listing,observed=observed,**options)


def publish(conn,execution,observed,originals,snapshot,archives):
    verify_runtime(conn,'generator')
    multi_store.verify_acl(conn)
    acquire_nav_data_operation_lock(conn)
    prior=multi_runtime.receipt(conn,execution)
    if prior:
        raise ValueError('HDFC_EXISTING_EXECUTION_REQUIRES_RECONCILIATION')
    identity=evidence.identity(conn,101763)
    if identity!=authority.source()['identity'] or snapshot['identity']!=identity:
        raise ValueError('HDFC_PUBLICATION_IDENTITY_DIFFERS')
    if snapshot['policy_hash']!=fingerprint(authority.policy()) or snapshot['engine_hash']!=method.engine_hash():
        raise ValueError('HDFC_PUBLICATION_SOURCE_DIFFERS')
    if snapshot['snapshot_id']!=fingerprint({k:v for k,v in snapshot.items() if k!='snapshot_id'}):
        raise ValueError('HDFC_PUBLICATION_SNAPSHOT_SEAL_DIFFERS')
    for row in originals:
        raw,url=row['content'],row['source_url']
        digest=hashlib.sha256(raw).hexdigest()
        conn.execute('INSERT INTO mf_multi_facts.sources VALUES(%s,%s,%s,%s) ON CONFLICT(content_hash) DO NOTHING',
                     (digest,url,len(raw),raw))
        actual=conn.execute("SELECT source_url,byte_length,encode(sha256(content),'hex') AS sha256 FROM mf_multi_facts.sources WHERE content_hash=%s",(digest,)).fetchone()
        if actual!={'source_url':url,'byte_length':len(raw),'sha256':digest}:
            raise ValueError('HDFC_ORIGINAL_REPLAY_DIFFERS')
    multi_store.append_exact(conn,'snapshots','snapshot_id',snapshot,
        {'amfi_scheme_code':101763,'content_hash':snapshot['content_hash'],
         'identity_content_hash':snapshot['identity_content_hash'],'disclosure_as_of':snapshot['disclosure_as_of']})
    known=conn.execute("SELECT 1 FROM mf_multi_facts.runs WHERE status<>'failed' AND document->'snapshot_ids' ? %s LIMIT 1",(snapshot['snapshot_id'],)).fetchone()
    return multi_store.append_run(conn,{'format':'registered-facts-run-v1','source_key':'hdfc-flexi-cap',
        'execution_id':execution,'observed_at':observed.isoformat(),'status':'unchanged' if known else 'updated',
        'policy_hash':fingerprint(authority.policy()),'snapshot_ids':[snapshot['snapshot_id']],
        'archives':archives,'source_rows_changed':0,'all_amc_data_complete':False,
        'verification_status':'needs_verification','discovery':authority.source()['discovery']})
