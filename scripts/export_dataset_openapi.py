#!/usr/bin/env python3
"""Export only additive dataset v1; never rewrite the frozen domain contracts."""
import argparse
import json
from pathlib import Path

from mfdataindia.datasets.app import create_app

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'contracts/mfdataindia-consumer-datasets-v1.json'


def render():
    schema = create_app('postgresql://contract:contract@127.0.0.1:1/contract').openapi()
    schema['paths'] = {k:v for k,v in schema['paths'].items() if k.startswith('/api/datasets/v1')}
    used = set()
    def references(value):
        if isinstance(value,dict):
            if '$ref' in value:
                name = value['$ref'].split('/')[-1]
                if name not in used:
                    used.add(name)
                    references(schema['components']['schemas'][name])
            for item in value.values():
                references(item)
        elif isinstance(value,list):
            for item in value:
                references(item)
    references(schema['paths'])
    schema['components']['schemas'] = {k:v for k,v in schema['components']['schemas'].items() if k in used}
    schema['info']['title'] = 'MFData Consumer Datasets'
    schema['info']['version'] = '1.0.0'
    schema['info']['x-contract-id'] = 'mfdata-consumer-datasets-v1'
    schema['x-authentication-boundary'] = 'Existing internal ingress and short-lived Google OIDC Cloud Run invoker authorization; TI separately enforces partner session and workload authorization. No direct browser access.'
    schema['x-read-only'] = True
    schema['x-snapshot-semantics'] = 'Fresh repeatable-read transaction per request, not a frozen cross-page snapshot.'
    return json.dumps(schema,indent=2,sort_keys=True) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    args = parser.parse_args()
    content = render()
    if args.check:
        if not CONTRACT.exists() or CONTRACT.read_text() != content:
            raise SystemExit('consumer dataset contract drift')
    else:
        CONTRACT.write_text(content)
