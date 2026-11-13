import json
from pathlib import Path
import subprocess
import sys

from mfdataindia.nse.smoke import run


def test_packaged_main_parser_and_migration_without_db_or_provider():
    assert run()['packaged_nse01_A_entrypoint']=='PASS'


def test_existing_both_image_dataset_smoke_retains_asgi_and_adds_offline_main():
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,str(root/'scripts/smoke_dataset_read_api.py')],
                          cwd=root,check=True,capture_output=True,text=True)
    receipts=[json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    assert receipts[0]['packaged_nse01_A_entrypoint']=='PASS'
    assert receipts[-1]['packaged_dataset_ASGI_entrypoint']=='PASS'
    workflow=(root/'.github/workflows/verify.yml').read_text()
    assert 'docker run --rm --entrypoint python "$target" /app/scripts/smoke_dataset_read_api.py' in workflow
