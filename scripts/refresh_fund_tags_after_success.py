#!/usr/bin/env python3
"""Run an existing source flow unchanged, then attempt one isolated tag refresh."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from mfdataindia.tagging.refresh import after_success  # noqa: E402

OPERATIONS = {
    'daily-amfi-refresh': ROOT / 'scripts/refresh_daily.py',
    'fund-family-build': ROOT / 'scripts/build_fund_family.py',
}


def _daily_trigger(stdout):
    try:
        receipt = json.loads(stdout)
        value = {
            'source_kind': 'daily-amfi-refresh',
            'source_content_hash': receipt['fetch']['content_sha256'],
            'observed_on': receipt['as_of'],
        }
        if receipt['status'] != 'DAILY_REFRESH_OK':
            raise ValueError('daily source receipt differs')
        return receipt, value
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None, None


def execute(operation, child_args, *, runner=subprocess.run, refresher=after_success,
            now=None):
    """Return process code/stdout/stderr; source success is never reclassified."""
    clock = (lambda: datetime.now(timezone.utc)) if now is None else now
    script = OPERATIONS[operation]
    child = runner(
        [sys.executable, str(script), *child_args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if child.returncode != 0:
        return child.returncode, child.stdout, child.stderr

    source_receipt_hash = hashlib.sha256(child.stdout.encode()).hexdigest()
    if operation == 'daily-amfi-refresh':
        receipt, trigger = _daily_trigger(child.stdout)
        if receipt is None:
            output = {
                'status': 'SOURCE_SUCCESS_RECEIPT_INVALID',
                'source_receipt_sha256': source_receipt_hash,
                'tag_refresh': {'state': 'NOT_REQUESTED_INVALID_SOURCE_RECEIPT'},
            }
        else:
            output = {
                **receipt,
                'source_receipt_sha256': source_receipt_hash,
                'tag_refresh': refresher(trigger),
            }
    elif '--dry-run' in child_args:
        output = {
            'status': 'FUND_FAMILY_DRY_RUN_OK',
            'source_receipt_sha256': source_receipt_hash,
            'tag_refresh': {'state': 'NOT_REQUESTED_DRY_RUN'},
        }
    else:
        tag_refresh = refresher({
            'source_kind': 'fund-family-build',
            'source_content_hash': source_receipt_hash,
            'observed_on': clock().date().isoformat(),
        })
        output = {
            'status': 'FUND_FAMILY_BUILD_OK',
            'source_receipt_sha256': source_receipt_hash,
            'tag_refresh': tag_refresh,
        }
    return 0, json.dumps(output, sort_keys=True) + '\n', child.stderr


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=sorted(OPERATIONS))
    args, child_args = parser.parse_known_args()
    code, stdout, stderr = execute(args.operation, child_args)
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
