"""Actual packaged offline main/parser smoke; no provider or database I/O."""
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from . import cli, contract as c, schema, store


def run():
    # Fully synthetic two-row universe; no credentials, retained real exports
    # or external files need to be shipped into a production image.
    cells = ['00000000001', 'SYNTH_G', 'RTA', 'AMC', 'INFX00000001', 'FIXTURE_AMC',
             'EQUITY', 'R', 'SYNTHETIC GROWTH', 'Y', 'P', '100', '100', '100000',
             '1', '15:00:00', '1', 'P', '', '1', '10000', '100', '10000', '1',
             '15:00:00', 'RTA', '1', '0', '1', '1', '1', '1', 'L1', 'SYN', '10',
             '01-01-2020', '', '0', '', '0', '0', 'SYNTH', '']
    second = [*cells]
    second[0], second[1] = '00000000002', 'SYNTH_OTHER'
    raw = ('|'.join(schema.HEADERS43)+'\n'+'|'.join(cells)+'\n'+'|'.join(second)+'\n').encode()
    meta = {'format': 'nse01-full-manifest-v1', 'family': 'SCH', 'environment': 'FIXTURE',
            'schema': schema.SCHEMA43, 'normalization': schema.VERSION, 'source_date': '2026-11-10',
            'scope': 'FULL', 'completeness_basis': 'SYNTHETIC_KNOWN_COMPLETE',
            'expected_sha256': sha256(raw).hexdigest(), 'expected_bytes': len(raw),
            'expected_rows': 2, 'reference': 'packaged-synthetic-two-row-v1'}
    walk = {'format': 'nse01-crosswalk-v1', 'version': 'synthetic-v1', 'amcs': {}, 'targets': []}
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError('offline smoke must not connect')
    assert store.MIGRATION.is_file() and store.MIGRATION.name == '045_nse_sch_offline.sql'
    with TemporaryDirectory() as folder:
        root = Path(folder)
        (root/'source.psv').write_bytes(raw)
        (root/'manifest.json').write_bytes(c.canonical(meta))
        (root/'crosswalk.json').write_bytes(c.canonical(walk))
        argv = ['--dry-run', '--file', str(root/'source.psv'), '--manifest', str(root/'manifest.json'),
                '--crosswalk', str(root/'crosswalk.json')]
        output = []
        assert cli.main(argv, environ=os.environ, connect=forbidden, output=output.append) == 0
        assert json.loads(output[-1])['source_rows'] == 2
        # Clean truncation exercises actual main, not --help/import/hash alone.
        (root/'source.psv').write_bytes(b'\n'.join(raw.splitlines()[:2])+b'\n')
        assert cli.main(argv, environ=os.environ, connect=forbidden, output=output.append) == 1
        assert json.loads(output[-1])['reason'] == 'FULL_BYTES_MISMATCH'
        (root/'source.psv').write_bytes(raw)
        (root/'manifest.json').write_bytes(c.canonical({**meta, 'family': 'SIP'}))
        assert cli.main(argv, environ=os.environ, connect=forbidden, output=output.append) == 1
        assert json.loads(output[-1])['reason'] == 'FAMILY_NOT_IMPLEMENTED'
    assert calls == []
    return {'packaged_nse01_A_entrypoint': 'PASS', 'database_connections': 0,
            'provider_requests': 0, 'operation_intents': 0, 'live_or_native_evidence': False}
