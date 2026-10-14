#!/usr/bin/env python3
"""Exact-approved existing-operator DEV execution; retain all backup/evidence data."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from mfdataindia.review.operator import (  # noqa: E402
    bootstrap_consumer_trust, load_operator_approval, trust_declaration,
    verify_checkout, verify_operator,
)
from mfdataindia.review.installation import cloud_install  # noqa: E402
from mfdataindia.review.policy import fingerprint  # noqa: E402
from mfdataindia.review.rollout import load_manifest  # noqa: E402
from mfdataindia.review.rollout_cloud import Cloud  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('bootstrap-consumer-trust', 'execute-rollout'))
    parser.add_argument('--operator-approval', type=Path, required=True)
    parser.add_argument('--approved-operator-hash', required=True)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--approved-manifest-hash')
    parser.add_argument('--work-directory', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.work_directory.exists() or not args.work_directory.is_absolute():
        raise ValueError('require new absolute private work directory; never replace evidence')
    # Bind operation before any cloud calls. Root/manifest paths are not shell input.
    if args.operation == 'bootstrap-consumer-trust':
        if args.manifest or args.approved_manifest_hash:
            raise ValueError('bootstrap has no rollout manifest or source operations')
        binding = fingerprint(trust_declaration())
        manifest = None
    else:
        if not args.manifest or not args.approved_manifest_hash:
            raise ValueError('execution requires exact rollout manifest approval')
        # Read the SHA only to validate the exact approved document below.
        import json as _json
        candidate = _json.loads(args.manifest.read_text())
        manifest = load_manifest(args.manifest, approved_hash=args.approved_manifest_hash,
                                 git_sha=candidate['git_sha'], root=ROOT)
        binding = manifest.seal
    approval = load_operator_approval(args.operator_approval, approved_hash=args.approved_operator_hash,
                                      operation=args.operation, binding_hash=binding)
    if manifest and approval.git_sha != manifest.git_sha:
        raise ValueError('operator approval and rollout source SHA differ')
    verify_checkout(ROOT, approval.git_sha)
    cloud = Cloud(account=approval.account)
    identity = verify_operator(cloud, approval)
    if manifest is None:
        print(json.dumps(bootstrap_consumer_trust(cloud, approval, args.work_directory), sort_keys=True))
        return 0
    if not os.environ.get('MFDATA_REVIEW_CLONE_ADMIN_DSN'):
        raise ValueError('isolated clone credential required in environment only')
    args.work_directory.mkdir(mode=0o700)
    with (args.work_directory / 'operator-execution.json').open('x') as handle:
        json.dump({'operator_approval_hash': approval.seal, 'manifest_hash': manifest.seal,
                   'identity': identity, 'backup_retention': 'PERSISTENT_LOCAL_AND_CREATE_ONLY_GCS'}, handle, sort_keys=True)
    module_spec = importlib.util.spec_from_file_location('review_rollout', ROOT / 'scripts/rollout_verified_review_dev.py')
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    try:
        module.rollout(manifest, cloud, args.work_directory, os.environ['MFDATA_REVIEW_CLONE_ADMIN_DSN'],
                       database_installer=cloud_install)
    except BaseException:
        module.archive(args.work_directory, '07-failure', manifest, 'failed',
                       {'status': 'NOT_CERTIFIED', 'backup_and_artifacts_retained': True,
                        'requires_operator_reconciliation': True})
        raise
    print(json.dumps({'status': 'PROVIDER_DEV_CERTIFIED', 'manifest_hash': manifest.seal,
                      'operator_approval_hash': approval.seal, 'evidence_directory': str(args.work_directory)}, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('operator review rollout failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
