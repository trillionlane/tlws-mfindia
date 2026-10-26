"""Fail-closed CI routing. No live credentials, network, deployment or SQL."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MODES = frozenset({'docs', 'targeted', 'full'})
WEB = frozenset('src/mfdataindia/web/' + name for name in (
    'index.html', 'fund.html', 'compare.html', 'style.css', 'index.js', 'fund.js',
    'compare.js', 'compare-cart.js', 'theme.js',
))
NATIVE_MODULES = frozenset({'tests/test_postgres_integration.py'})
INVENTORY = ROOT / 'docs/CI_RISK_POLICY_STEP_INVENTORY_20261026.json'
BASELINE_PATHS = frozenset(json.loads(
    (ROOT / 'docs/CI_RISK_POLICY_PARENT_AUTHORITY_20261026.json').read_text())['files'])


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def git(*args, root=ROOT):
    return subprocess.check_output(['git', *args], cwd=root)


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
        raise ValueError('exact lowercase Git SHA required')
    return value


def valid_path(path):
    if (not isinstance(path, str) or not path or '\x00' in path or '\\' in path
            or path.startswith('/') or any(x in {'', '.', '..'} for x in path.split('/'))):
        raise ValueError('invalid changed path')
    return path


def prose(path):
    return path == 'README.md' or (
        path.startswith('docs/') and path.endswith('.md') and path not in BASELINE_PATHS
        and not path.startswith(('docs/evidence/', 'docs/runbooks/'))
    )


def classify(paths, *, regular=True):
    paths = [valid_path(path) for path in paths]
    if not paths or not regular:
        return 'full'
    if all(prose(path) for path in paths):
        return 'docs'
    if all(prose(path) or path in WEB for path in paths):
        return 'targeted'
    return 'full'


def changed_paths(raw):
    # --no-renames exposes BOTH old and new names (D/A), never drops a path.
    parts = raw.decode('utf-8', errors='strict').split('\0')
    if parts[-1] != '' or (len(parts) - 1) % 2:
        raise ValueError('malformed Git name-status inventory')
    result = []
    for status, path in zip(parts[:-1:2], parts[1:-1:2]):
        if status not in {'A', 'D', 'M', 'T'}:
            raise ValueError('unsupported Git change status')
        result.append(valid_path(path))
    if len(result) != len(set(result)):
        raise ValueError('duplicate changed path')
    return sorted(result)


def plan(event_name, event, github_sha, *, root=ROOT):
    checkout = git('rev-parse', 'HEAD', root=root).decode().strip()
    if sha(checkout) != sha(github_sha):
        raise ValueError('event/checkout Git mismatch')
    if event_name in {'push', 'workflow_dispatch'}:
        if event.get('ref') != 'refs/heads/main':
            raise ValueError('main is the only non-PR CI ref')
        if event_name == 'push' and sha(event.get('after')) != checkout:
            raise ValueError('push Git mismatch')
        return {'version': 1, 'event': event_name, 'checkout': checkout,
                'head': checkout, 'base': None, 'paths': [], 'mode': 'full'}
    if event_name != 'pull_request':
        raise ValueError('unsupported CI event')
    pr = event['pull_request']
    base, head = sha(pr['base']['sha']), sha(pr['head']['sha'])
    if pr['base']['ref'] != 'main':
        raise ValueError('PR must target main')
    for commit in (base, head):
        subprocess.run(['git', 'merge-base', '--is-ancestor', commit, checkout],
                       cwd=root, check=True, capture_output=True)
    changes = git('diff', '--name-status', '--no-renames', '-z', base + '...' + head, '--', root=root)
    paths = changed_paths(changes)
    regular = not any(status in {b'D', b'T'} for status in changes.split(b'\x00')[:-1:2])
    # Type changes, deleted symlinks and gitlinks never get a fast route.
    for commit in (base, head, checkout):
        if paths:
            entries = git('ls-tree', '-z', commit, '--', *paths, root=root).split(b'\0')
            regular &= all(row.startswith(b'100644 blob ') or row.startswith(b'100755 blob ')
                           for row in entries if row)
            regular &= all(not row.startswith(b'100755 ') for row in entries if row)
    return {'version': 1, 'event': event_name, 'checkout': checkout,
            'head': head, 'base': base, 'paths': paths, 'mode': classify(paths, regular=regular)}


def environment_plan(environ=os.environ, *, root=ROOT):
    event_path = Path(environ['GITHUB_EVENT_PATH'])
    if event_path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('oversize GitHub event')
    return plan(environ['GITHUB_EVENT_NAME'], json.loads(event_path.read_text()),
                environ['GITHUB_SHA'], root=root)


def verify_plan(value, environ=os.environ):
    if (environ.get('CI_PLAN_RESULT') != 'success'
            or environ.get('CI_MODE') != value['mode']
            or environ.get('CI_BINDING') != digest(value)):
        raise ValueError('missing, failed, stale or changed CI plan')
    return value


def required_ids(job, mode, inventory):
    if mode not in MODES or job not in {'tests', 'verify'}:
        raise ValueError('invalid CI route')
    return {row['id']: mode in row['modes'] for row in inventory[job]}


def gate(job, mode, steps, inventory):
    expected = required_ids(job, mode, inventory)
    expected['policy'] = True
    expected['reduced'] = (mode != 'full') if job == 'tests' else (mode == 'docs')
    if set(steps) != set(expected):
        raise ValueError('missing/unknown CI step')
    for key, required in expected.items():
        desired = 'success' if required else 'skipped'
        if steps[key].get('outcome') != desired or steps[key].get('conclusion') != desired:
            raise ValueError(f'CI step {key} must be {desired}')


def docs_checks(value, *, root=ROOT):
    if value['mode'] != 'docs':
        raise ValueError('prose-only plan required')
    for name in value['paths']:
        valid_path(name)
        if not prose(name):
            raise ValueError('not prose')
        path = root / PurePosixPath(name)
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError('regular prose file required')
            if '\x00' in path.read_text(encoding='utf-8'):
                raise ValueError('NUL in prose')
    subprocess.run(['git', 'diff', '--check', value['base'] + '...' + value['head']],
                   cwd=root, check=True)
    result = subprocess.run(['git', 'grep', '-nEI', '-e',
        r'-----BEGIN ([A-Z0-9 ]+ )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|AKIA[0-9A-Z]{16}',
        '--', '.', ':!scripts/scan_tracked_secrets.sh'], cwd=root,
        capture_output=True)
    if result.returncode != 1:
        raise ValueError('credential scan found a match or failed')


def require_full_policy_jobs(jobs):
    """Full-only read admission. Reduced PRs cannot be called whole native proof."""
    inventory = json.loads(INVENTORY.read_text())
    names = {'plan': 'Plan CI coverage', 'tests': 'PostgreSQL tests, contracts, and audit',
             'verify': 'Test, audit, and build'}
    if len(jobs) != 3 or {j.get('name') for j in jobs} != set(names.values()):
        raise ValueError('all three distinct CI jobs required')
    by_name = {job['name']: job for job in jobs}
    for key, label in names.items():
        job = by_name[label]
        if job.get('status') != 'completed' or job.get('conclusion') != 'success':
            raise ValueError('all CI jobs must succeed')
        steps = job.get('steps')
        if not isinstance(steps, list) or not steps or len({s.get('name') for s in steps}) != len(steps):
            raise ValueError('complete unique steps required')
        for step in steps:
            desired = 'skipped' if key != 'plan' and step.get('name') == 'Run reduced PR checks' else 'success'
            if step.get('status') != 'completed' or step.get('conclusion') != desired:
                raise ValueError('unexpected failed, partial or skipped full step')
        actual = {s['name'] for s in steps}
        if key == 'plan':
            required = {'CI plan: full', 'Classify exact change'}
        else:
            required = {row['label'] for row in inventory[key]}
            required |= {'Validate CI plan', 'Run reduced PR checks', 'CI policy admitted: full'}
        if not required <= actual:
            raise ValueError('missing complete FULL step inventory')
    return {'both_jobs_all_required_steps': 'PASS', 'policy': 'full',
            'sole_permitted_skip': 'Run reduced PR checks', 'plan_job': 'PASS'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['plan', 'validate', 'docs', 'gate'])
    parser.add_argument('--job', choices=['tests', 'verify'])
    args = parser.parse_args()
    value = environment_plan()
    if args.operation == 'plan':
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write(f"mode={value['mode']}\nbinding={digest(value)}\n")
    else:
        verify_plan(value)
        if args.operation == 'docs':
            docs_checks(value)
        if args.operation == 'gate':
            gate(args.job, value['mode'], json.loads(os.environ['CI_STEPS']),
                 json.loads(INVENTORY.read_text()))
    print(json.dumps({'ci_policy': value, 'binding': digest(value), 'operation': args.operation}))


if __name__ == '__main__':
    main()
