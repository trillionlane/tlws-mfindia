"""Offline complete-census validation; never opens a connection or spends an identity.

Usage: python -m mfdataindia.tagging --input synthetic-census.json
The input is a JSON list of exact policy ROW_KEYS. No SQL, DSN or environment
payload is accepted. Database-backed census/publication uses the trusted store
seam; there is no live mutation command in this sprint.
"""
import argparse
import json
from pathlib import Path

from . import policy as p


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        p.require(key not in value, 'DUPLICATE_CENSUS_MEMBER')
        value[key] = item
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.input.open('rb') as file:
            raw = file.read(67108865)
        p.require(len(raw) <= 67108864, 'FUND_CENSUS_TOO_LARGE')
        rows = json.loads(raw, object_pairs_hook=_pairs)
        candidate = p.prepare(rows)
        report = {k: v for k, v in candidate.items()
                  if k not in {'assessments', 'definitions', 'observations'}}
        print(json.dumps({'state': 'OFFLINE_VALIDATED_NOT_PUBLISHED', **report}, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError) as error:
        reason = str(error) if isinstance(error, p.TaggingError) else 'INVALID_CENSUS_INPUT'
        print(json.dumps({'state': 'LOCAL_VALIDATION_FAILED_NO_DATABASE', 'reason': reason}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
