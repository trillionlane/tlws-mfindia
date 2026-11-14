from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
FILTER = ROOT / "scripts" / "tag_publication_log.jq"


def receipt() -> dict[str, object]:
    return {
        "format": "mfdata-tag-refresh-attempt-v1",
        "state": "PUBLISHED",
        "tag_snapshot_id": "a" * 64,
        "source_publication_id": "b" * 64,
        "source_publication_sequence": 1,
    }


@pytest.mark.parametrize(
    "entry",
    [
        {"jsonPayload": receipt()},
        {"textPayload": json.dumps(receipt(), sort_keys=True)},
    ],
)
def test_exact_execution_log_accepts_structured_and_legacy_text_receipt(
    entry: dict[str, object],
) -> None:
    result = subprocess.run(
        ["jq", "-cer", "-f", str(FILTER)],
        input=json.dumps([entry]),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == receipt()


@pytest.mark.parametrize("entries", [[], [{"jsonPayload": receipt()}] * 2])
def test_exact_execution_log_rejects_missing_or_duplicate_receipts(
    entries: list[dict[str, object]],
) -> None:
    result = subprocess.run(
        ["jq", "-cer", "-f", str(FILTER)],
        input=json.dumps(entries),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
