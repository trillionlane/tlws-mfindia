#!/usr/bin/env python3
"""Fixed recovery verifier: accepted nine-route profile, no new canary writes."""
import json
import os
import sys

from mfdataindia.calculators.certification import certify
from mfdataindia.review.rollout import Manifest

if __name__ == "__main__":
    try:
        m = Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"])
        print(json.dumps(certify(m, os.environ, recovery=True), sort_keys=True))
    except Exception as exc:
        print("calculator recovery certification failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
