#!/usr/bin/env python3
"""Separate additive profile contract; original review-v1 pin stays unchanged."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from mfdataindia.review.app import create_research_app  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    path = ROOT / 'contracts/mfdataindia-research-publication-v1.json'
    serialized = json.dumps(create_research_app('postgresql://contract:contract@127.0.0.1:1/contract').openapi(),
                            indent=2, sort_keys=True) + '\n'
    if args.check:
        if not path.exists() or path.read_text() != serialized:
            raise SystemExit('research publication OpenAPI contract drift')
    else:
        path.write_text(serialized)


if __name__ == '__main__':
    main()
