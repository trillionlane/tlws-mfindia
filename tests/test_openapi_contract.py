import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_committed_openapi_contract_is_current_and_complete() -> None:
    subprocess.run(
        [sys.executable, "scripts/export_openapi.py", "--check"], cwd=ROOT, check=True
    )
    committed = (ROOT / "contracts" / "mfdataindia-openapi-v1.json").read_text()

    contract = json.loads(committed)
    assert contract["info"]["x-contract-id"] == "mfdataindia-json-read-v1"
    assert contract["info"]["x-contract-version"] == 1
    assert contract["x-mfdata-read-only"] is True
    assert contract["x-mfdata-api-path-count"] == 19
    api_paths = {path for path in contract["paths"] if path.startswith("/api/")}
    assert len(api_paths) == 19
    assert {
        method
        for path in api_paths
        for method in contract["paths"][path]
        if method != "parameters"
    } == {"get"}
