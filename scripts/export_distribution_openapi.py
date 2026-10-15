#!/usr/bin/env python3
"""Export/check the additive private 29-route reported-observations contract."""

import argparse
import json
from pathlib import Path
from mfdataindia.review.app import create_distribution_observations_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("contracts/mfdataindia-distribution-observations-v1.json"),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = (
        json.dumps(
            create_distribution_observations_app(
                "postgresql://fixture:fixture@127.0.0.1:1/fixture"
            ).openapi(),
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    if args.check:
        if not args.output.exists() or args.output.read_text() != content:
            raise SystemExit("distribution observation contract drift")
    else:
        args.output.write_text(content)
