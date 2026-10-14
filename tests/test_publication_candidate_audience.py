"""Tagged request target is distinct from IAM audience; no live IAM claims."""

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization

from mfdataindia.review import publication_certification as module
from mfdataindia.review.policy import fingerprint
from test_verified_review_auth import trust
from test_verified_review_rollout import manifest

SERVICE = "https://service.a.run.app"
TAG = "https://cert-rollout---service.a.run.app"


@pytest.mark.parametrize("origin", [SERVICE, TAG])
def test_explicit_default_audience_for_service_and_tag(origin):
    assert module.request_origins({"REVIEW_ORIGIN": origin, "REVIEW_AUDIENCE": SERVICE}) == (
        origin,
        SERVICE,
    )


@pytest.mark.parametrize(
    "origin,audience",
    [
        (TAG, TAG),
        (TAG, "https://other.a.run.app"),
        ("https://other.a.run.app", SERVICE),
        (TAG, ""),
        (TAG, SERVICE + ",A=B"),
        ("https://evil.invalid", SERVICE),
        ("https://cert.rollout---service.a.run.app", SERVICE),
    ],
)
def test_mismatched_or_missing_audience_fails_before_network(origin, audience):
    with pytest.raises(ValueError):
        module.request_origins({"REVIEW_ORIGIN": origin, "REVIEW_AUDIENCE": audience})


def test_actual_certifier_mints_for_service_but_requests_zero_traffic_tag(monkeypatch):
    key, verifier = trust()
    config = {
        "public_key": key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode(),
        "issuer": verifier.issuer,
        "audience": verifier.audience,
        "caller": verifier.caller,
        "allowed_scopes": sorted(verifier.allowed_scopes),
    }
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()

    def read(path):
        if str(path) == "/trust/config.json":
            return json.dumps(config)
        if str(path) == "/signer/key.pem":
            return private
        pytest.fail("unexpected file access")

    monkeypatch.setattr(Path, "read_text", read)

    class TaggedBoundaryReached(Exception):
        pass

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            assert self.trust_env is False
            assert url.startswith(TAG + "/api/review/v1/funds/")
            assert kwargs["headers"]["X-Serverless-Authorization"] == "Bearer fixture-iam-token"
            assert kwargs["allow_redirects"] is False
            raise TaggedBoundaryReached()

    def identity(session, audience):
        assert audience == SERVICE and isinstance(session, Session)
        return "fixture-iam-token"

    monkeypatch.setattr(module.requests, "Session", Session)
    monkeypatch.setattr(module, "metadata_identity", identity)
    m = manifest(trust_hash=fingerprint(config))
    with pytest.raises(TaggedBoundaryReached):
        module.certify(
            m, {"DEPLOYMENT_GIT_SHA": m.git_sha, "REVIEW_ORIGIN": TAG, "REVIEW_AUDIENCE": SERVICE}
        )
