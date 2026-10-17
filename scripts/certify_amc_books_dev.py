#!/usr/bin/env python3
"""Certify registered books over signed private HTTP and unchanged core routes."""

import argparse
import json
import os
import sys

from mfdataindia.enrichment.book_certification import certify
from mfdataindia.review.rollout import Manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active", action="store_true")
    parser.add_argument("--recovery", action="store_true")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                certify(
                    Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"]),
                    os.environ,
                    active=args.active,
                    recovery=args.recovery,
                ),
                sort_keys=True,
            ),
            flush=True,
        )
    except Exception as error:
        print("AMC book certification failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
