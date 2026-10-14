#!/usr/bin/env python3
import json
import os
import sys

from mfdataindia.analytics.certification import certify
from mfdataindia.review.rollout import Manifest

if __name__ == "__main__":
    try:
        m = Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"])
        print(json.dumps(certify(m, os.environ), sort_keys=True))
    except Exception as exc:
        print("calendar HTTP certification failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
