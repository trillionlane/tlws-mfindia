#!/usr/bin/env python3
"""The explicitly allocated existing six-task DEV book workload; no overrides."""
import argparse
import json
import os
from pathlib import Path

from mfdataindia.enrichment import allocated_amc_workload as workload


def main(argv=None, *, source_root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--validate', action='store_true',
                      help='Validate local allocation/inputs only; no database or provider I/O')
    mode.add_argument('--check-installed', action='store_true',
                      help='Read-only installed-policy and synthetic PDF check; no source acquisition/publication')
    args = parser.parse_args(argv)
    root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[1]
    # Preserve the actual os._Environ boundary and isolate the request snapshot.
    environment = dict(os.environ)
    stage = 'validation'
    effect = 'NOT_SUBMITTED'
    try:
        document, dsn, index = workload.validate(environment, root)
        if args.validate:
            result = {'format': 'allocated-amc-preflight-v1',
                      'allocation_id': document['id'], 'status': 'local_validation_passed',
                      'database_and_provider_readiness': 'UNVERIFIED',
                      'database_connections': 0, 'provider_requests': 0}
        else:
            stage = 'database_policy'
            workload.verify_database(dsn, document)
            if args.check_installed:
                stage = 'pdf_compatibility'
                workload.verify_pdf_runtime()
                result = {'format': 'allocated-amc-installed-check-v1',
                          'allocation_id': document['id'], 'status': 'installed_policy_verified',
                          'allocation_sha256': workload.ALLOCATION_SHA256,
                          'git_sha': environment['DEPLOYMENT_GIT_SHA'],
                          'database': workload.DATABASE, 'principal': workload.GENERATOR,
                          'book_configuration_sha256': document['book_configuration_sha256'],
                          'dependency': document['dependency']['version'],
                          'synthetic_bounded_pdf': 'PASS', 'provider_requests': 0,
                          'database_mutations': 0, 'consumer_acceptance': False}
            else:
                stage = 'source_execution'
                effect = 'UNKNOWN'
                result = workload.execute(dsn, environment, index, document)
                if result['status'] == 'completed':
                    effect = 'COMPLETED_RUNTIME_RESULT'
        stage = 'reporting'
        print(json.dumps(result, sort_keys=True), flush=True)
        if result['status'] == 'failed':
            raise SystemExit(1)
    except Exception as error:
        # A runtime/transport error is not proof that no append/commit occurred.
        result = {'format': 'allocated-amc-failure-v1',
                  'allocation_id': workload.ALLOCATION_ID, 'status': 'failed',
                  'stage': stage, 'reason': workload.safe_reason(error, stage),
                  'source_effect': effect,
                  'disposition': 'STOP_NO_AUTOMATIC_REPLAY'}
        try:
            print(json.dumps(result, sort_keys=True), flush=True)
        except Exception:
            pass  # Reporting failure never retries the source operation.
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
