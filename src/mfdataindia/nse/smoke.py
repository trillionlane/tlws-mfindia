"""Actual packaged offline main/parser smoke; no provider or database I/O."""
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from . import cli, contract as c, store
from .fixture import FULL_MANIFEST_JSON, FULL_SOURCE


def run():
    raw = FULL_SOURCE
    meta = c.decode(FULL_MANIFEST_JSON)
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
        assert json.loads(output[-1])['source_rows'] == 7
        # Clean truncation exercises actual main, not --help/import/hash alone.
        short = b'\n'.join(raw.splitlines()[:2]) + b'\n'
        (root/'source.psv').write_bytes(short)
        assert cli.main(argv, environ=os.environ, connect=forbidden, output=output.append) == 1
        assert json.loads(output[-1])['reason'] == 'FULL_BYTES_MISMATCH'
        (root/'manifest.json').write_bytes(c.canonical({
            **meta, 'expected_sha256': sha256(short).hexdigest(),
            'expected_bytes': len(short), 'expected_rows': 1,
        }))
        assert cli.main(argv, environ=os.environ, connect=forbidden, output=output.append) == 1
        assert json.loads(output[-1])['reason'] == 'FULL_MANIFEST_UNTRUSTED'
        (root/'source.psv').write_bytes(raw)
        (root/'manifest.json').write_bytes(c.canonical({**meta, 'family': 'SIP'}))
        assert cli.main(argv, environ=os.environ, connect=forbidden, output=output.append) == 1
        assert json.loads(output[-1])['reason'] == 'FAMILY_NOT_IMPLEMENTED'
    assert calls == []
    return {'packaged_nse01_A_entrypoint': 'PASS', 'database_connections': 0,
            'provider_requests': 0, 'operation_intents': 0, 'live_or_native_evidence': False}
