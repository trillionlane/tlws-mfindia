"""Record intentional current changes; no historical document is rewritten."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = '6e074b0d32fe5923d8adba384d9784d08ae4afaa'
MODIFIED = {
    'Dockerfile':'Select the additive dataset/core factory in the unchanged default Uvicorn command; preserve all legacy core routes and explicit worker/review entrypoints.',
    'scripts/migrate.py':'Add the isolated, observable eligible-reader enrollment reconciliation hook after existing successful migration/calculation work.',
    '.github/workflows/verify.yml':'Inspect and exercise the actual APP image default factory in the existing image check, plus one provider-free dataset ASGI smoke inside the existing BOTH-image loop; no new job, credentials, resources, gate or skipped check.',
    'tests/dev_reader_compatibility_history.py':'Apply this finite current whole-file inverse before older historical release assertions.',
    'tests/test_dev_reader_compatibility_history.py':'Retain every complete old compatibility assertion against its exact predecessor; separately test actual current dataset changes.',
}
rows = {}
for name, reason in MODIFIED.items():
    old = subprocess.check_output(['git','show',BASE+':'+name],cwd=ROOT)
    current = (ROOT / name).read_bytes()
    rows[name] = {'previous_sha256':hashlib.sha256(old).hexdigest(),
                  'current_sha256':hashlib.sha256(current).hexdigest(),'reason':reason}
    if name == '.github/workflows/verify.yml':
        history = json.loads((ROOT / 'docs/RO03_CURRENT_SOURCE_CHANGES_20261101.json').read_text())['modified'][name]
        rows[name]['historical_passthrough_sha256'] = [history['current_sha256'],history['previous_sha256']]
document = {'format':'dataset-coverage-current-source-changes-v2','base':BASE,
            'historical_only':True,'runtime_authority':False,'modified':rows,
            'no_ignored_regions_or_test_assertion_waivers':True}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,default=ROOT/'docs/DATASET_COVERAGE_CURRENT_SOURCE_CHANGES_20261104.json')
path = parser.parse_args().output
with path.open('x') as stream:
    json.dump(document,stream,sort_keys=True,indent=2)
    stream.write('\n')
print(hashlib.sha256(path.read_bytes()).hexdigest())
