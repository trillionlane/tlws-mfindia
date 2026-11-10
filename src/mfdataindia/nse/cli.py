"""Offline file import, dry-run and inspect-only recovery. No NSE HTTP client."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from . import contract as c, mapping, parser, store


def _read(path, maximum):
    with path.open('rb') as handle:
        value = handle.read(maximum + 1)
    c.require(len(value) <= maximum, 'INPUT_LIMIT_EXCEEDED')
    return value


def reserve(directory, operation_id, payload):
    """Create-only local WAL, fsynced before connection/transaction submission.

    A damaged, lost or existing operation record is not permission to replay.
    No DSN, raw cells, SQL, authentication material or filesystem paths stored.
    """
    c.operation(operation_id)
    c.require(directory.is_dir() and not directory.is_symlink(), 'JOURNAL_DIRECTORY_REQUIRED')
    path = directory / (operation_id + '.json')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise c.ImportError('OPERATION_SPENT') from None
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(c.canonical({'format': 'nse01-operation-intent-v1', 'operation_id': operation_id,
                                      'candidate_sha256': c.digest(payload), 'disposition': 'NO_REPLAY'}))
            handle.flush()
            os.fsync(handle.fileno())
        parent = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except OSError:
        # Never erase/reuse an incomplete durable intent.
        raise c.ImportError('INTENT_PERSISTENCE_FAILED') from None


def main(argv=None, *, environ=None, connect=psycopg.connect, output=print):
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--file', type=Path)
    args.add_argument('--manifest', type=Path)
    args.add_argument('--crosswalk', type=Path)
    mode = args.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--import-file', action='store_true')
    mode.add_argument('--inspect', action='store_true')
    args.add_argument('--operation')
    args.add_argument('--journal-dir', type=Path)
    args.add_argument('--expected-head')
    options = args.parse_args(argv)
    env = dict(os.environ if environ is None else environ)  # Snapshot _Environ at real boundary.
    conn = None
    stage = 'validation'
    report = {'format': 'nse01-import-result-v1', 'state': 'LOCAL_VALIDATION_FAILED_NO_DISPATCH',
              'effect': 'NO_PUBLICATION', 'stage': stage, 'disposition': 'CORRECT_LOCAL_CANDIDATE',
              'consumer_acceptance': 'NOT_OBSERVED'}
    code = 1
    try:
        if options.inspect:
            c.operation(options.operation)
            c.require(not any((options.file, options.manifest, options.crosswalk, options.expected_head)), 'INVALID_INSPECT_INPUT')
            report.update(operation_id=options.operation, effect='UNKNOWN', state='UNKNOWN', disposition='INSPECT_ONLY')
        else:
            c.require(all((options.file, options.manifest, options.crosswalk)), 'COMPLETE_INPUT_REQUIRED')
            meta = c.decode(_read(options.manifest, 256 * 1024))
            candidate = parser.parse(_read(options.file, c.MAX_BYTES), meta)
            walk = mapping.crosswalk(c.decode(_read(options.crosswalk, 256 * 1024)))
            report.update(source_sha256=candidate.manifest['expected_sha256'],
                          source_rows=len(candidate.rows), state='VALIDATED_NOT_PUBLISHED')
            if options.dry_run:
                c.require(not options.operation and not options.journal_dir and not options.expected_head, 'INVALID_DRY_RUN_INPUT')
                report.update(disposition='NO_DATABASE_OR_OPERATION_EFFECTS', layout_evidence='SYNTHETIC_FIXTURE_ONLY',
                              mapping='UNVERIFIED_DATABASE_INPUTS', installation='UNVERIFIED',
                              production_completeness='UNVERIFIED')
                output(json.dumps(report, sort_keys=True))
                return 0
            c.operation(options.operation)
            c.require(options.journal_dir is not None, 'JOURNAL_DIRECTORY_REQUIRED')
            if options.expected_head:
                c.operation(options.expected_head)
            report['operation_id'] = options.operation
            reserve(options.journal_dir, options.operation, {'manifest': meta, 'crosswalk': walk,
                                                           'expected_head': options.expected_head})
        stage = 'connect'
        c.require(bool(env.get('MFDATAINDIA_DSN')), 'CONNECTION_CONFIGURATION_MISSING')
        conn = connect(env['MFDATAINDIA_DSN'], row_factory=dict_row, connect_timeout=5)
        stage = 'inspect' if options.inspect else 'publication'
        report.update(state='UNKNOWN', effect='UNKNOWN', disposition='INSPECT_ONLY')
        if options.inspect:
            report = store.inspect(conn, options.operation)
        else:
            report = store.publish(conn, candidate, walk, options.operation, expected_head=options.expected_head)
        code = 0
    except store.OutcomeError as error:
        report = error.report
    except BaseException as error:
        report.update(reason=str(error) if isinstance(error, c.ImportError) else
                      'LOCAL_INPUT_FAILED' if stage == 'validation' else 'EXECUTION_UNCONFIRMED', stage=stage)
        # Inspect absence/failure cannot say that the original import never ran.
        if options.inspect or stage == 'publication':
            report.update(state='UNKNOWN', effect='UNKNOWN', disposition='INSPECT_ONLY')
    finally:
        if conn is not None:
            try:
                conn.close()
            except BaseException:
                report.update(reason='CONNECTION_CLEANUP_FAILED', disposition='INSPECT_ONLY')
                code = 1  # Retain known committed/unknown effect, never "no import".
    try:
        output(json.dumps(report, sort_keys=True))
    except BaseException:
        return 1
    return code
