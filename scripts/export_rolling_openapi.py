#!/usr/bin/env python3
"""Additive nine-route contract; all earlier factory/contract pins retained."""

import argparse
import json
from pathlib import Path

from mfdataindia.review.app import create_rolling_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=Path("contracts/mfdataindia-rolling-analytics-v1.json")
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    document = create_rolling_app("postgresql://fixture:fixture@127.0.0.1:1/fixture").openapi()
    content = json.dumps(document, sort_keys=True, indent=2) + "\n"
    if args.check:
        if not args.output.exists() or args.output.read_text() != content:
            raise SystemExit("rolling analytics contract drift")
    else:
        args.output.write_text(content)
