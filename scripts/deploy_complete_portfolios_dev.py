#!/usr/bin/env python3
"""Explicit DEV append-only portfolio install/import/publish. --help is dormant."""

import argparse
import json
import os
import sys
import requests
from mfdataindia.enrichment.portfolio_deployment import execute
from mfdataindia.review.rollout import Manifest
from mfdataindia.enrichment.pdf_source_dispatch import require_source_authority

if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("operation", choices=["install", "import", "publish"])
        args = parser.parse_args()
        if args.operation in ("import", "publish"):
            require_source_authority()
        with requests.Session() as session:
            result = execute(
                Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"]),
                os.environ,
                args.operation,
                session,
            )
        print(json.dumps(result, sort_keys=True))
    except Exception as error:
        print("complete portfolio operation failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
