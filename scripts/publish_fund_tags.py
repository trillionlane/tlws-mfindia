#!/usr/bin/env python3
"""Publish factual tags from the latest verified committed source receipt."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mfdataindia.tagging.refresh import publish_verified  # noqa: E402


def main() -> int:
    result = publish_verified()
    print(json.dumps(result, sort_keys=True))
    if result["state"] in {"PUBLISHED", "UNCHANGED"}:
        return 0
    if result["state"] in {"SOURCE_RECEIPT_UNAVAILABLE", "SOURCE_BUSY"}:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
