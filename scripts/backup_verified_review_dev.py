#!/usr/bin/env python3
"""Read-only regional PG18 backup; never execute restore or source DML."""
import json
import os
import sys
import tempfile
from pathlib import Path

import requests

from mfdataindia.review.regional_backup import regional_export
from mfdataindia.review.rollout import Manifest

if __name__ == '__main__':
    try:
        manifest = Manifest.model_validate_json(os.environ['REVIEW_MANIFEST'])
        if os.environ['DEPLOYMENT_GIT_SHA'] != manifest.git_sha:
            raise ValueError('regional backup SHA differs')
        with requests.Session() as session:
            session.trust_env = False
            directory = Path(tempfile.mkdtemp(prefix='mf-review-regional-backup-')) / 'export'
            print(json.dumps(regional_export(manifest, os.environ, directory, session), sort_keys=True))
    except Exception as exc:
        print('regional review backup failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
