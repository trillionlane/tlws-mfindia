#!/usr/bin/env python3
"""Report read-only DEV prerequisites; never access secret payloads or deploy."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from mfdataindia.review.preflight import SIGNER_SECRET, SOURCE_SECRET, TRUST_SECRET, inventory  # noqa: E402
from mfdataindia.review.rollout_cloud import Cloud  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--git-sha', required=True)
    parser.add_argument('--trust-version', type=int)
    parser.add_argument('--signer-version', type=int)
    parser.add_argument('--source-version', type=int)
    args = parser.parse_args(argv)
    versions = {name: version for name, version in (
        (TRUST_SECRET, args.trust_version), (SIGNER_SECRET, args.signer_version),
        (SOURCE_SECRET, args.source_version)) if version is not None}
    report = inventory(Cloud(), git_sha=args.git_sha, versions=versions)
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report['status'] == 'PREREQUISITES_OBSERVED_NOT_CERTIFIED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
