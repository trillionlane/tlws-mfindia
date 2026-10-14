#!/usr/bin/env python3
"""Private zero-retry optional-schema job; preserve original source rows."""
import json
import os
import sys
from pathlib import Path

from mfdataindia.review.installation import install_job
from mfdataindia.review.rollout import Manifest

if __name__ == '__main__':
    try:
        manifest = Manifest.model_validate_json(os.environ['REVIEW_MANIFEST'])
        if os.environ['DEPLOYMENT_GIT_SHA'] != manifest.git_sha:
            raise ValueError('installation job SHA differs')
        print(json.dumps(install_job(manifest, os.environ, Path('/app')), sort_keys=True))
    except Exception as exc:
        print('private review installation failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
