#!/usr/bin/env python3
"""Fixed existing AMC workload: local validation, installed check or normal task."""
import argparse
import json
import os
from pathlib import Path

from mfdataindia.enrichment import ingestion_workload as workload


def main(argv=None, *, source_root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--validate', action='store_true')
    modes.add_argument('--check-installed', action='store_true')
    parser.add_argument('--kotak-only', action='store_true')
    args = parser.parse_args(argv)
    root = Path(source_root) if source_root else Path(__file__).resolve().parents[1]
    stage, effect, result = 'validation', 'NOT_SUBMITTED', None
    try:
        environment = dict(os.environ)
        document, dsn, index = workload.validate(environment, root, kotak_only=args.kotak_only)
        if args.validate:
            result = {'status':'local_validation_passed','database_and_provider_readiness':'UNVERIFIED',
                      'database_connections':0,'provider_requests':0,'live_consumer_acceptance':False}
        else:
            stage = 'installed_policy'
            workload.verify_database(dsn, document)
            if args.check_installed:
                result = {'status':'installed_policy_verified','database_mutations':0,
                          'provider_requests':0,'live_consumer_acceptance':False}
            else:
                stage, effect = 'source_execution', 'UNKNOWN'
                result = workload.execute(dsn, environment, document, index)
                effect = result['discovery']['publication_effect']
        stage = 'reporting'
        print(json.dumps(result, sort_keys=True), flush=True)
        if result['status'] == 'failed':
            raise SystemExit(1)
    except Exception:
        # A broken stdout/journal/cleanup cannot erase the strongest result.
        safe = {'format':'amc-discovery-failure-v1','status':'failed','stage':stage,
                'publication_effect':effect,'reason':'AMC_TASK_STOPPED',
                'disposition':'INSPECT_ONLY_NO_REPLAY','live_consumer_acceptance':False}
        if result and 'discovery' in result:
            safe['discovery'] = result['discovery']
        try:
            print(json.dumps(safe, sort_keys=True), flush=True)
        except Exception:
            pass
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
