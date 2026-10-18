#!/usr/bin/env python3
"""Ordinary source-only discovery evidence; no DB, retry, fallback or publication."""

import argparse
import os
import sys

from mfdataindia.enrichment.source_operations import instant
from mfdataindia.enrichment.source_operations_probe import probe
from mfdataindia.review.evidence_transport import render_evidence
from mfdataindia.review.rollout import Manifest, seal_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=instant, required=True)
    args = parser.parse_args()
    manifest = Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"])
    if os.environ.get("DEPLOYMENT_GIT_SHA") != manifest.git_sha or "MFDATAINDIA_DSN" in os.environ:
        raise ValueError("exact source image and no database credential required")
    print(render_evidence(seal_evidence(manifest, "ordinary-source-catalogue-probe", probe(args.as_of))), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("source catalogue probe failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
