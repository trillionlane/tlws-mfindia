"""Real private-HTTP provider certification, not consumer RBAC acceptance.

Only the manifest's approved in_review/no-op-proposal canary may append events.
No source DML, destructive negative probes or fabricated 'reviewed' decisions.
"""
from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

import jwt
import psycopg
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg.rows import dict_row

from mfdataindia.review import schemas, store
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.policy import fingerprint, policy
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.rollout import Manifest, seal_evidence, service_origin


def certify(manifest: Manifest, *, base_url: str, audience: str, reader_dsn: str,
            private_key: str, trust: dict, session, identity_token: str, evidence_level='DEV_PRIVATE_HTTP') -> dict:
    if evidence_level not in ('DEV_PRIVATE_HTTP', 'RESTORED_CLONE_HTTP'):
        raise ValueError('invalid certification evidence level')
    base_url, audience = service_origin(base_url), service_origin(audience)
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError('trust content differs from approval')
    verifier = AssertionVerifier(**{**trust, 'allowed_scopes': frozenset(trust['allowed_scopes'])})
    key = serialization.load_pem_private_key(private_key.encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey) or key.public_key().public_bytes_raw() != verifier.key.public_bytes_raw():
        raise ValueError('certifier must use the existing approved consumer signer')

    def token(scopes, *, expired=False):
        now = int(time.time()) - (600 if expired else 0)
        return jwt.encode({'iss': verifier.issuer, 'aud': verifier.audience, 'caller': verifier.caller,
                           'sub': manifest.actor, 'scope': scopes, 'iat': now, 'nbf': now, 'exp': now + 240,
                           'jti': uuid4().hex}, key, algorithm='EdDSA')

    def request(method, path, *, scopes=('review:read',), expected=200, body=None, idempotency=None, expired=False, no_assertion=False):
        headers = {'X-Serverless-Authorization': 'Bearer ' + identity_token}
        if not no_assertion:
            headers['Authorization'] = 'Bearer ' + token(list(scopes), expired=expired)
        if idempotency:
            headers['Idempotency-Key'] = idempotency
        response = session.request(method, base_url + '/api/review/v1' + path, headers=headers, json=body,
                                   timeout=30, allow_redirects=False)
        if response.status_code != expected or response.headers.get('Cache-Control') != 'no-store':
            raise ValueError('private endpoint/status/cache certification failed: ' + method + ' ' + path)
        return response.json()

    request('GET', '/funds', no_assertion=True, expected=401)
    request('GET', '/funds', expired=True, expected=401)
    received_policy = schemas.PolicyResponse.model_validate(request('GET', '/policies/' + manifest.policy_hash))
    if received_policy.document != policy() or received_policy.policy_hash != manifest.policy_hash:
        raise ValueError('registered policy mismatch')
    queued = schemas.QueueResponse.model_validate(request('GET', '/funds?limit=1'))
    if not queued.items:
        raise ValueError('empty provider queue')
    if queued.next_after_code is not None:
        page = schemas.QueueResponse.model_validate(request('GET', '/funds?limit=1&after_code=' + str(queued.next_after_code)))
        if page.items and page.items[0].code <= queued.next_after_code:
            raise ValueError('queue pagination is not advancing')
    request('GET', '/funds?limit=101', expected=422)
    evidence = []
    coverage = set()
    for code in manifest.cohort:
        detail_raw = request('GET', '/funds/' + str(code))
        detail = schemas.ReviewResponse.model_validate(detail_raw)
        # Parsed defaults cannot count as an observed explicit null.
        quality = detail_raw['context']['nav_quality']
        if 'signals' not in quality:
            raise ValueError('signals field must be explicitly present')
        if quality['signals'] is None and quality['assessment_status'] != 'not_assessed':
            raise ValueError('null signals require an unassessed fund')
        raw = request('GET', '/funds/' + str(code) + '/analytics')
        analytics = schemas.ResultResponse.model_validate(raw)
        with psycopg.connect(reader_dsn, row_factory=dict_row) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            verify_runtime(conn, 'api')
            if conn.execute('SELECT current_user AS name').fetchone()['name'] != 'mfdata_verified_reader':
                raise ValueError('certification requires dedicated reader login')
            snapshot = store.capture(conn, code)
            expected = store.derive(snapshot, policy())
            local = store.get_result(conn, code, analytics.result.result_id)
            expected_detail = schemas.ReviewResponse.model_validate(store.get_review(conn, code)).model_dump(mode='json')
        expected_wire = schemas.ResultDocument.model_validate(expected).model_dump(mode='json')
        if (raw['result'] != expected_wire or local['result'] != expected or local['input_status'] != 'current' or analytics.input_status != 'current'
                or detail_raw != expected_detail or detail.current_input_hash != expected['input_hash']
                or analytics.comparison_eligible != local['comparison_eligible']):
            raise ValueError('served analytics differ from current deterministic source derivation')
        exact = request('GET', '/funds/' + str(code) + '/analytics?result_id=' + expected['result_id'])
        if exact != raw:
            raise ValueError('exact result retrieval differs')
        evidence.append({'code': code, 'result_id': expected['result_id'], 'input_hash': expected['input_hash'],
                         'option': snapshot['identity']['option_type'], 'quality_status': quality['assessment_status'],
                         'explicit_null_signals': quality['signals'] is None, 'comparison_eligible': analytics.comparison_eligible,
                         'metrics_unavailable': sorted(expected['unavailable'])})
        if snapshot['identity']['option_type'] == 'GROWTH' and analytics.comparison_eligible:
            coverage.add('growth_eligible')
        if snapshot['identity']['option_type'] != 'GROWTH' and not analytics.comparison_eligible and all(v is None for v in expected['metrics'].values()):
            coverage.add('unsupported_option')
        if quality['signals'] is None:
            coverage.add('explicit_null_signals')
        if quality['signals']:
            coverage.add('quality_signalled')
        if expected['unavailable']:
            coverage.add('unavailable_metric')
    if not set(manifest.required_coverage) <= coverage:
        raise ValueError('approved cohort does not supply required certification coverage')
    code = manifest.canary_code
    detail = request('GET', '/funds/' + str(code))
    result = request('GET', '/funds/' + str(code) + '/analytics')['result']
    prefix = '/funds/' + str(code)
    stem = 'rollout-' + manifest.rollout_id
    body = {'expected_version': 0, 'expected_input_hash': result['input_hash'], 'result_id': result['result_id'],
            'status': 'in_review', 'disposition': 'not_determined',
            'reason': 'DEV certification only; not a fund correctness decision: ' + manifest.rollout_id,
            'evidence': ['rollout://' + manifest.rollout_id + '/' + manifest.seal]}
    # No real human decision may be overwritten. Retry accepts only this exact
    # historical canary's two events, and not a later user decision/proposal.
    history = detail['history']
    if detail['version'] not in (0, 1, 2) or len(history) != detail['version'] or any(
            h['actor'] != manifest.actor or h['caller'] != verifier.caller for h in history):
        raise ValueError('canary contains existing non-certification decisions')
    request('POST', prefix + '/reviews', body=body, idempotency=stem + '-denied', expected=403)
    first = request('POST', prefix + '/reviews', scopes=('review:admin',), body=body, idempotency=stem + '-review')
    schemas.EventResponse.model_validate(first)
    if first != request('POST', prefix + '/reviews', scopes=('review:admin',), body=body, idempotency=stem + '-review') or first['version'] != 1:
        raise ValueError('review replay failed')
    request('POST', prefix + '/reviews', scopes=('review:admin',), body=body, idempotency=stem + '-conflict', expected=409)
    proposal = {k: v for k, v in body.items() if k not in ('status', 'disposition')}
    proposal.update(expected_version=1, fields=[{'field': 'scheme_name', 'proposed_value': detail['identity']['scheme_name']}])
    second = request('POST', prefix + '/enrichment-proposals', scopes=('review:admin',), body=proposal, idempotency=stem + '-proposal')
    if second != request('POST', prefix + '/enrichment-proposals', scopes=('review:admin',), body=proposal, idempotency=stem + '-proposal') or second['version'] != 2:
        raise ValueError('proposal replay failed')
    final = request('GET', prefix + '?after_version=1&limit=1')
    if (final['status'] != 'in_review' or final['version'] != 2 or final['needs_revalidation']
            or final['history'][0]['document'].get('execution') != 'not_executable'):
        raise ValueError('canary history/revalidation mismatch')
    request('GET', '/funds?status=in_review&limit=1')
    return seal_evidence(manifest, 'certification', {'level': evidence_level, 'origin': base_url,
                         'audience': audience, 'endpoints': 6, 'cohort': evidence,
                         'coverage_observed': sorted(coverage), 'coverage_required': manifest.required_coverage,
                         'canary_versions': [1, 2], 'source_writes': 0, 'insights_rbac_certified': False})


def metadata_identity(session, audience):
    response = session.get('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity',
                           params={'audience': audience}, headers={'Metadata-Flavor': 'Google'}, timeout=5, allow_redirects=False)
    if response.status_code != 200 or not response.text.strip():
        raise ValueError('workload identity unavailable')
    return response.text.strip()


def job_main(manifest, environment):
    import json
    from mfdataindia.review.privileges import verify_runtime
    if environment['DEPLOYMENT_GIT_SHA'] != manifest.git_sha:
        raise ValueError('job deployment SHA differs')
    if environment['REVIEW_OPERATION'] == 'generate':
        with psycopg.connect(environment['MFDATAINDIA_DSN'], row_factory=dict_row) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            verify_runtime(conn, 'generator')
            snapshots = [store.capture(conn, c) for c in sorted(manifest.cohort)]
        with psycopg.connect(environment['MFDATAINDIA_DSN'], row_factory=dict_row) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            verify_runtime(conn, 'generator')
            results = store.publish(conn, snapshots, policy())
            repeated = store.publish(conn, snapshots, policy())
            if repeated != results:
                raise ValueError('publication replay differs')
        return seal_evidence(manifest, 'generation', {'level': 'DEV_DATABASE',
                             'results': [{k: r[k] for k in ('code', 'result_id', 'input_hash', 'engine_fingerprint')} for r in results], 'source_writes': 0})
    trust = json.loads(Path('/trust/config.json').read_text())
    with requests.Session() as session:
        session.trust_env = False
        audience = service_origin(environment['REVIEW_AUDIENCE'])
        return certify(manifest, base_url=environment['REVIEW_ORIGIN'], audience=audience,
                       reader_dsn=environment['MFDATAINDIA_DSN'], private_key=Path('/signer/key.pem').read_text(),
                       trust=trust, session=session, identity_token=metadata_identity(session, audience))
