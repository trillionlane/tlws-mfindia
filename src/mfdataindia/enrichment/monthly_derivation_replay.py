"""Explicit 6.20.1 -> 6.21.4 equivalent-derivation observation.

The old snapshot and its provenance are returned unchanged. Only the three
recorded decoder VERSION labels may differ. Text/engine/policy hashes, every
financial/identity field, unavailable reason and all other bytes must agree.
This does not accept changed facts, repair prior values or relax uniqueness.
"""
from copy import deepcopy

from mfdataindia.enrichment import monthly_schemas
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.store import Conflict

PATHS = ('detail_extraction', 'changes_extraction', 'identity_extraction')


def comparison(retained, candidate):
    old = monthly_schemas.MonthlySnapshot.model_validate(retained).model_dump(mode='json')
    new = monthly_schemas.MonthlySnapshot.model_validate(candidate).model_dump(mode='json')
    for value in (old, new):
        if fingerprint({k:v for k,v in value.items() if k != 'snapshot_id'}) != value['snapshot_id']:
            raise Conflict('monthly equivalent replay seal differs')
    if old == new:
        return None
    left, right = deepcopy(old), deepcopy(new)
    left.pop('snapshot_id')
    right.pop('snapshot_id')
    for path in PATHS:
        if (left[path]['extractor_version'], right[path]['extractor_version']) != ('6.20.1', '6.21.4'):
            raise Conflict('monthly equivalent replay dependency transition differs')
        left[path].pop('extractor_version')
        right[path].pop('extractor_version')
    if left != right:
        raise Conflict('monthly equivalent replay facts or provenance differ')
    return {'format':'monthly-equivalent-derivation-v1',
            'retained_snapshot_id':old['snapshot_id'], 'candidate_snapshot_id':new['snapshot_id'],
            'reason':'ONLY_THREE_DECODER_VERSION_LABELS_DIFFER',
            'original_dependency':'6.20.1','new_dependency':'6.21.4',
            'retained_snapshot_changed':False}


def resolve(conn, candidate, observations):
    row = conn.execute('''SELECT document FROM mf_monthly_facts.snapshots
        WHERE amfi_scheme_code=%s AND content_hash=%s AND identity_document_id=%s
        AND policy_hash=%s AND identity_hash=%s''',
        (candidate['code'], candidate['content_hash'], candidate['identity_document_id'],
         candidate['policy_hash'], candidate['identity_hash'])).fetchone()
    if row is None:
        raise Conflict('monthly equivalent replay has no retained publication')
    evidence = comparison(row['document'], candidate)
    if evidence is not None:
        observations.append(evidence)
    return row['document']
