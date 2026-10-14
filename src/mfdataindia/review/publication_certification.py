"""Private read-only certification of explicit current/historical publication."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from uuid import uuid4

import jwt
import psycopg
import requests
from psycopg.rows import dict_row

from mfdataindia.review import publication, schemas
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.rollout import seal_evidence, service_origin


def request_origins(environment):
    origin = service_origin(environment.get('REVIEW_ORIGIN',''))
    audience = service_origin(environment.get('REVIEW_AUDIENCE',''))
    # Cloud Run ID tokens always name the default service audience, including
    # requests sent to a zero-traffic tag URL. Never accept another service or
    # a tagged URL as the audience. No custom audiences are configured here.
    host = audience.removeprefix('https://')
    if '---' in host or (origin != audience and not re.fullmatch(
            r'https://[a-z0-9](?:[a-z0-9-]*[a-z0-9])?---'+re.escape(host), origin)):
        raise ValueError('publication target and default service audience differ')
    return origin, audience


def certify(manifest, environment):
    origin, audience = request_origins(environment)
    trust = json.loads(Path('/trust/config.json').read_text())
    if environment['DEPLOYMENT_GIT_SHA'] != manifest.git_sha or fingerprint(trust) != manifest.trust_hash:
        raise ValueError('publication certifier source/trust differs')
    verifier = AssertionVerifier(**{**trust,'allowed_scopes':frozenset(trust['allowed_scopes'])})
    private = Path('/signer/key.pem').read_text()
    now = int(time.time())
    assertion = jwt.encode({'iss':verifier.issuer,'aud':verifier.audience,'caller':verifier.caller,
        'sub':manifest.actor,'scope':['review:read'],'iat':now,'nbf':now,'exp':now+240,'jti':uuid4().hex},private,algorithm='EdDSA')
    with requests.Session() as session:
        session.trust_env = False
        iam = metadata_identity(session,audience)
        headers = {'X-Serverless-Authorization':'Bearer '+iam,'Authorization':'Bearer '+assertion}
        results = []
        for code in manifest.cohort:
            path = origin+'/api/review/v1/funds/'+str(code)+'/publication'
            denied = session.get(path,headers={'X-Serverless-Authorization':'Bearer '+iam},timeout=30,allow_redirects=False)
            if denied.status_code != 401:
                raise ValueError('publication assertion denial not observed')
            response = session.get(path,headers=headers,timeout=30,allow_redirects=False)
            if response.status_code != 200 or response.headers.get('Cache-Control') != 'no-store':
                raise ValueError('publication private HTTP/status/cache failed')
            raw = response.json()
            schemas.PublicationResponse.model_validate(raw)
            if 'signals' not in raw['current_context']['nav_quality']:
                raise ValueError('quality signals field missing')
            with psycopg.connect(environment['MFDATAINDIA_DSN'],row_factory=dict_row) as conn:
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                verify_runtime(conn,'api')
                if conn.execute('SELECT current_user AS role').fetchone()['role'] != 'mfdata_verified_reader':
                    raise ValueError('publication certification needs dedicated reader')
                expected = schemas.PublicationResponse.model_validate(publication.get_publication(conn,code)).model_dump(mode='json')
            if raw != expected or raw['last_certified'] is None:
                raise ValueError('publication differs from coherent retained-source projection')
            results.append({'code':code,'state':raw['state'],'certificate_id':raw['last_certified']['certificate_id'],
                'result_id':raw['last_certified']['result_id'],'current_input_hash':raw['current_input_hash'],
                'current_comparison_eligible':raw['current_comparison_eligible']})
    return seal_evidence(manifest,'publication-http',{'level':'DEV_PRIVATE_HTTP','origin':origin,'audience':audience,
                         'publication_policy_hash':fingerprint(publication.policy()),'cohort':results,
                         'endpoint':'GET /api/review/v1/funds/{code}/publication','source_writes':0,
                         'records_appended':0,'insights_rbac_certified':False})
