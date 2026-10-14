#!/usr/bin/env python3
"""Separate ten-route contract; all earlier profile pins remain unchanged."""

import argparse
import json
from pathlib import Path
from mfdataindia.review.app import create_calculator_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=Path("contracts/mfdataindia-forward-calculators-v1.json")
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = (
        json.dumps(
            create_calculator_app("postgresql://fixture:fixture@127.0.0.1:1/fixture").openapi(),
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    if args.check:
        if not args.output.exists() or args.output.read_text() != content:
            raise SystemExit("forward calculator contract drift")
    else:
        args.output.write_text(content)
