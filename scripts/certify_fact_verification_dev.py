#!/usr/bin/env python3
"""Certify private source history/verification reads; never create a human review."""

import argparse
import json
import os
import sys
from mfdataindia.enrichment.fact_review_certification import certify
from mfdataindia.review.rollout import Manifest

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery", action="store_true")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                certify(
                    Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"]),
                    os.environ,
                    recovery=args.recovery,
                ),
                sort_keys=True,
            )
        )
    except Exception as error:
        print("fact-verification certification failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
