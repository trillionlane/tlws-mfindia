import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mfdataindia.review.app import create_review_app
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.schemas import ProposalMutation, ReviewMutation


def trust(scopes=frozenset({'review:read','review:admin'})):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    verifier = AssertionVerifier(public_key=public,issuer='fixture-issuer',audience='fixture-audience',caller='insights',allowed_scopes=scopes)
    return private,verifier


def assertion(private, **overrides):
    now=int(time.time())
    claims={'iss':'fixture-issuer','aud':'fixture-audience','sub':'admin-1','caller':'insights',
            'scope':['review:read','review:admin'],'iat':now,'nbf':now,'exp':now+120,'jti':'fixture-id-123'}
    claims.update(overrides)
    return jwt.encode(claims,private,algorithm='EdDSA')


def test_valid_assertion_and_pinned_scope_capability():
    private,verifier=trust()
    principal=verifier.verify(assertion(private))
    assert principal.actor=='admin-1' and principal.caller=='insights'
    _,reader=trust(frozenset({'review:read'}))
    # Reader trust cannot be upgraded by an admin claim, even with its own key.
    other_private,reader=trust(frozenset({'review:read'}))
    with pytest.raises(ValueError,match='scopes'):
        reader.verify(assertion(other_private))


@pytest.mark.parametrize('changes',[
    {'iss':'attacker'}, {'aud':'wrong'}, {'aud':['fixture-audience','other']},
    {'caller':'tdw'}, {'sub':''}, {'scope':['review:admin','source:write']},
    {'scope':['review:read','review:read']}, {'exp':1}, {'iat':1,'nbf':1,'exp':9999999999},
    {'exp':True}, {'role':'admin'}, {'sub':123}, {'jti':''},
])
def test_invalid_assertions(changes):
    private,verifier=trust()
    with pytest.raises((ValueError,jwt.PyJWTError)):
        verifier.verify(assertion(private,**changes))


def test_wrong_signer_unsigned_and_symmetric_tokens():
    _,verifier=trust()
    private,_=trust()
    with pytest.raises(jwt.PyJWTError):
        verifier.verify(assertion(private))
    for algorithm,key in [('none',None),('HS256','fixture-secret-only-not-a-real-secret-123')]:
        with pytest.raises(ValueError):
            verifier.verify(jwt.encode({'role':'admin'},key,algorithm=algorithm))


def test_dormant_api_is_no_connection_and_no_mutation(monkeypatch):
    monkeypatch.delenv('MFDATAINDIA_REVIEW_TRUST_FILE',raising=False)
    with TestClient(create_review_app('postgresql://contract:contract@127.0.0.1:1/contract')) as client:
        for path in ['/api/review/v1/funds','/api/review/v1/funds/1',
                     '/api/review/v1/funds/1/analytics','/api/review/v1/policies/'+('a'*64)]:
            response=client.get(path,headers={'X-Role':'admin','X-Actor':'admin-1'})
            assert response.status_code==503
            assert response.headers['Cache-Control']=='no-store'


def test_review_and_proposal_request_bounds():
    base={'expected_version':0,'expected_input_hash':'a'*64,'result_id':'b'*64,'reason':'Inspected','evidence':['fixture://evidence']}
    assert ReviewMutation(**base,status='reviewed',disposition='consistent_with_evidence')
    for changes in ({'expected_version':True},{'reason':' '},{'evidence':[]},{'actor':'forged'},{'evidence':['x','x']}):
        with pytest.raises(ValidationError):
            ReviewMutation(**{**base,**changes,'status':'reviewed','disposition':'consistent_with_evidence'})
    for fields in ([{'field':'nav','proposed_value':'999'}],
                   [{'field':'aum','proposed_value':'123'}]*2):
        with pytest.raises(ValidationError):
            ProposalMutation(**base,fields=fields)


def test_opt_in_schema_is_not_automatic_and_existing_contracts_unchanged():
    from mfdataindia.store.postgres import DEFAULT_MIGRATIONS
    from mfdataindia.api.app import create_app
    assert not any('023' in name for name in DEFAULT_MIGRATIONS)
    root=Path(__file__).resolve().parents[1]
    assert not list((root/'sql').glob('023_*.sql'))
    assert (root/'sql/optional/023_verified_fund_review.sql').exists()
    assert len(create_app('postgresql://contract:contract@127.0.0.1:1/contract').openapi()['paths'])==19
    enhancement=create_review_app('postgresql://contract:contract@127.0.0.1:1/contract').openapi()
    assert len(enhancement['paths'])==6
    assert 'HTTPBearer' in enhancement['components']['securitySchemes']
