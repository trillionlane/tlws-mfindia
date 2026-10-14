import hashlib
import json

import pytest

from mfdataindia.review.image_provenance import verify_image

BASE = 'asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia'


class Cloud:
    calls = 0
    def command(self, *args):
        assert args == ('auth', 'print-access-token')
        self.calls += 1
        return 'fixture-token-only'


def setup_fixture(*, sha='a' * 40, platform='amd64', index=False, status=200, corrupt=False):
    config = json.dumps({'os': 'linux', 'architecture': platform,
                         'config': {'Labels': {'org.opencontainers.image.revision': sha}}}).encode()
    config_digest = 'sha256:' + hashlib.sha256(config).hexdigest()
    manifest = json.dumps({'config': {'digest': config_digest}}).encode()
    digest = 'sha256:' + hashlib.sha256(manifest).hexdigest()
    blobs = {config_digest: config, digest: manifest}
    if index:
        outer = json.dumps({'manifests': [{'digest': digest, 'platform': {'os': 'linux', 'architecture': 'amd64'}}]}).encode()
        digest = 'sha256:' + hashlib.sha256(outer).hexdigest()
        blobs[digest] = outer
    class Session:
        calls = []
        trust_env = True
        def get(self, url, **kwargs):
            self.calls.append(url)
            assert kwargs['allow_redirects'] is False
            assert url.startswith('https://asia-south1-docker.pkg.dev/v2/')
            data = blobs[url.rsplit('/', 1)[-1]]
            return type('Response', (), {'status_code': status, 'content': data + b'corrupt' if corrupt else data})()
    return BASE + '@' + digest, Session()


@pytest.mark.parametrize('index', [False, True])
def test_digest_and_source_label_verified_without_layers(index):
    image, session = setup_fixture(index=index)
    result = verify_image(Cloud(), image, 'a' * 40, session=session)
    assert result['layers_downloaded'] == 0
    assert session.trust_env is False
    assert len(session.calls) == (3 if index else 2)


@pytest.mark.parametrize('options', [{'sha': 'b' * 40}, {'platform': 'arm64'}, {'status': 302}, {'corrupt': True}])
def test_provenance_drift_or_redirect_is_not_accepted(options):
    image, session = setup_fixture(**options)
    with pytest.raises(ValueError):
        verify_image(Cloud(), image, 'a' * 40, session=session)


def test_unapproved_registry_has_no_credential_access():
    cloud = Cloud()
    cloud.calls = 0
    with pytest.raises(ValueError):
        verify_image(cloud, 'https://evil.invalid/image', 'a' * 40)
    assert cloud.calls == 0
