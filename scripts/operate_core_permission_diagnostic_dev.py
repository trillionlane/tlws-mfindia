#!/usr/bin/env python3
"""Run ONE approved create-only DEV catalogue-permission diagnostic, never an old job or rollout."""

import argparse
from pathlib import Path
import sys

from mfdataindia.review.core_permission_backend import Backend, load_approval
from mfdataindia.review.core_permission_controller import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval", required=True)
    parser.add_argument("--approval-hash", required=True)
    args = parser.parse_args()
    approval = load_approval(args.approval, args.approval_hash)
    backend = Backend(approval, Path(__file__).resolve().parents[1])
    try:
        run(approval, backend)
    finally:
        backend.session.close()
    print("diagnostic finished; not performance or rollout acceptance", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("diagnostic controller failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
