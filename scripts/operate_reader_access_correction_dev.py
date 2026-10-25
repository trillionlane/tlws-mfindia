#!/usr/bin/env python3
"""ONE separately exact-approved owner grant. Scope approval is not live G5."""
import argparse
from pathlib import Path
import sys

from mfdataindia.review.reader_access_transport_backend import Backend, load_approval
from mfdataindia.review.reader_access_transport_controller import run


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
    print("exact column grant verified; NOT FS13 or performance acceptance", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("owner controller failed:" + type(error).__name__ + ";STOP_NO_RETRY", file=sys.stderr)
        raise SystemExit(1) from None
