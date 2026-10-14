#!/usr/bin/env python3
"""Read-only private workload, no signer fetch by provider or local operator."""
import json
import os
import sys

from mfdataindia.review.publication_certification import certify
from mfdataindia.review.rollout import Manifest

if __name__ == '__main__':
    try:
        manifest = Manifest.model_validate_json(os.environ['REVIEW_MANIFEST'])
        print(json.dumps(certify(manifest,os.environ),sort_keys=True))
    except Exception as exc:
        print('research publication certification failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1)
