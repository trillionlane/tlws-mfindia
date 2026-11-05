#!/usr/bin/env python3
"""Allocated six-source workload; explicit single-task Kotak recovery mode."""
import argparse
import json
import os
from pathlib import Path
from mfdataindia.enrichment import kotak_current_workload as workload
from mfdataindia.enrichment import allocated_amc_workload as prior


def main(argv=None, *, source_root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate', action='store_true')
    parser.add_argument('--kotak-only', action='store_true', help='One-task recovery of the fixed Kotak source only; no other AMC acquisition')
    args = parser.parse_args(argv)
    root = Path(source_root) if source_root else Path(__file__).resolve().parents[1]
    stage, effect = 'validation', 'NOT_SUBMITTED'
    try:
        environment = dict(os.environ)
        document, dsn, index = workload.validate(environment, root, kotak_only=args.kotak_only)
        if args.validate:
            result = {'format': 'kotak-current-preflight-v2', 'status': 'local_validation_passed',
                      'database_and_provider_readiness': 'UNVERIFIED', 'database_connections': 0,
                      'provider_requests': 0}
        else:
            stage = 'database_policy'
            prior.verify_database(dsn, document)
            stage, effect = 'source_execution', 'UNKNOWN'
            result = workload.execute(dsn, environment, document, index)
        print(json.dumps(result, sort_keys=True), flush=True)
        if result['status'] == 'failed':
            raise SystemExit(1)
    except Exception:
        # Runtime effects remain uncertain unless an actual receipt was read.
        result = {'format': 'kotak-current-failure-v2', 'status': 'failed',
                  'stage': stage, 'source_effect': effect,
                  'reason': {'validation': 'KOTAK_CURRENT_VALIDATION_FAILED',
                             'database_policy': 'KOTAK_CURRENT_DATABASE_CHECK_FAILED',
                             'source_execution': 'KOTAK_CURRENT_EFFECT_UNCONFIRMED'}[stage],
                  'disposition': 'STOP_NO_AUTOMATIC_REPLAY'}
        try:
            print(json.dumps(result, sort_keys=True), flush=True)
        except Exception:
            pass
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
