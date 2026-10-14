"""Verify immutable OCI config/SHA without downloading large local image layers."""
from __future__ import annotations

import base64
import hashlib
import json
import re

import requests


def verify_image(cloud, image: str, expected_sha: str, *, session=None):
    match = re.fullmatch(r'asia-south1-docker\.pkg\.dev/(trillionlane-dev/tlws-mf-data-dev/mfdataindia(?:-review-tools)?)@(sha256:[0-9a-f]{64})', image)
    if not match or not re.fullmatch('[0-9a-f]{40}', expected_sha):
        raise ValueError('require approved immutable provider image and source SHA')
    token = cloud.command('auth', 'print-access-token')
    headers = {'Authorization': 'Basic ' + base64.b64encode(('oauth2accesstoken:' + token).encode()).decode(),
               'Accept': 'application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json'}
    owned = session is None
    session = session or requests.Session()
    session.trust_env = False
    repository, digest = match.groups()
    base = 'https://asia-south1-docker.pkg.dev/v2/' + repository
    def fetch(kind, wanted):
        if not re.fullmatch('sha256:[0-9a-f]{64}', wanted):
            raise ValueError('invalid OCI descriptor digest')
        response = session.get(base + '/' + kind + '/' + wanted, headers=headers, timeout=30, allow_redirects=False)
        if response.status_code != 200 or len(response.content) > 1024 * 1024:
            raise ValueError('immutable OCI metadata unavailable or oversized')
        if 'sha256:' + hashlib.sha256(response.content).hexdigest() != wanted:
            raise ValueError('OCI metadata bytes do not match digest')
        return json.loads(response.content)
    try:
        manifest = fetch('manifests', digest)
        if 'manifests' in manifest:
            matches = [m for m in manifest['manifests'] if m.get('platform', {}).get('os') == 'linux'
                       and m.get('platform', {}).get('architecture') == 'amd64']
            if len(matches) != 1:
                raise ValueError('require exactly one approved Linux amd64 image')
            manifest = fetch('manifests', matches[0]['digest'])
        config = fetch('blobs', manifest['config']['digest'])
        if (config.get('os') != 'linux' or config.get('architecture') != 'amd64'
                or config.get('config', {}).get('Labels', {}).get('org.opencontainers.image.revision') != expected_sha):
            raise ValueError('immutable image platform/source provenance differs')
        return {'image': image, 'git_sha': expected_sha, 'platform': 'linux/amd64', 'layers_downloaded': 0}
    finally:
        if owned:
            session.close()
