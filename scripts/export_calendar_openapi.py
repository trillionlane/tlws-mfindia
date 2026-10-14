#!/usr/bin/env python3
"""Additive eight-route contract; original six/seven/read-v2 pins are retained."""

import argparse
import json
from pathlib import Path

from mfdataindia.review.app import create_calendar_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=Path("contracts/mfdataindia-calendar-analytics-v1.json")
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    document = create_calendar_app("postgresql://fixture:fixture@127.0.0.1:1/fixture").openapi()
    content = json.dumps(document, sort_keys=True, indent=2) + "\n"
    if args.check:
        if not args.output.exists() or args.output.read_text() != content:
            raise SystemExit("calendar analytics contract drift")
    else:
        args.output.write_text(content)
