#!/usr/bin/env python3
"""Export/check the closed custom sustainability-values contract."""
import argparse
import json
from pathlib import Path

from mfdataindia.tagging.values_app import create_app
from mfdataindia.tagging.values_reads import PREFIX


def contract(document):
    return {
        'openapi': document['openapi'],
        'paths': {key: value for key, value in document['paths'].items()
                  if key.startswith(PREFIX)},
        'components': {'schemas': {
            key: value for key, value in document['components']['schemas'].items()
            if key.startswith(('Values', 'ActivityExposure', 'Screen'))
        }},
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=Path('contracts/mfdataindia-fund-values-v1.json'))
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    content = json.dumps(contract(create_app(
        'postgresql://fixture:fixture@127.0.0.1:1/fixture').openapi()),
        sort_keys=True, indent=2) + '\n'
    if args.check:
        if not args.output.exists() or args.output.read_text() != content:
            raise SystemExit('fund values contract drift')
    else:
        args.output.write_text(content)
