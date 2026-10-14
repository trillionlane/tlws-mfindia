#!/usr/bin/env python3
"""Private Cloud Run job entrypoint; no credentials/assertions in logs."""
import json
import os
import sys

from mfdataindia.review.certification import job_main
from mfdataindia.review.rollout import Manifest

if __name__ == '__main__':
    try:
        manifest = Manifest.model_validate_json(os.environ['REVIEW_MANIFEST'])
        if os.environ['REVIEW_OPERATION'] not in ('generate', 'certify'):
            raise ValueError('invalid bounded job operation')
        print(json.dumps(job_main(manifest, os.environ), sort_keys=True))
    except Exception as exc:
        print('private review certification failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
