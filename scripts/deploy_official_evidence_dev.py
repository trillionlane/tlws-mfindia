#!/usr/bin/env python3
"""Explicit fixed DEV installation/import worker, never an API or source overwriter."""
import json
import os
import sys
import requests
import argparse
from mfdataindia.enrichment.deployment import execute
from mfdataindia.review.rollout import Manifest
from mfdataindia.enrichment.pdf_source_dispatch import require_source_authority

if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("operation", choices=["install", "import"])
        args = parser.parse_args()
        if args.operation == "import":
            require_source_authority()
        manifest = Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"])
        with requests.Session() as session:
            print(json.dumps(execute(manifest, os.environ, args.operation, session), sort_keys=True))
    except Exception as exc:
        print("official evidence deployment failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
