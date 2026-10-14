#!/usr/bin/env python3
import json
import os
import sys
import argparse
from mfdataindia.enrichment.certification import certify
from mfdataindia.review.rollout import Manifest

if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description="Private DEV evidence HTTP certification")
        parser.add_argument("--recovery", action="store_true")
        args = parser.parse_args()
        manifest = Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"])
        print(json.dumps(certify(manifest, os.environ, recovery=args.recovery), sort_keys=True))
    except Exception as exc:
        print("official evidence certification failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
