#!/usr/bin/env python3
"""Run one exact-approved DEV reader stage; never resume or retry it."""

import argparse
import json
from pathlib import Path
import re
import sys

from mfdataindia.review.broad_reader_direct_delivery_backend import Backend
from mfdataindia.review.broad_reader_direct_rollout_controller import run, validate_approval
from mfdataindia.review.policy import fingerprint


def unique(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("DUPLICATE_DIRECT_APPROVAL_MEMBER")
        document[key] = value
    return document


def load_approval(path, expected_hash, *, source_root):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not re.fullmatch(
            r"[0-9a-f]{64}", expected_hash):
        raise ValueError("EXACT_DIRECT_APPROVAL_FILE_AND_HASH_REQUIRED")
    document = json.loads(path.read_text(), object_pairs_hook=unique)
    if path.resolve() != Path(document["root"]) / "approval.json" or fingerprint(
            document) != expected_hash:
        raise ValueError("DIRECT_APPROVAL_PATH_OR_CONTENT_DRIFT")
    validate_approval(document, source_root=source_root)
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval", required=True)
    parser.add_argument("--approval-hash", required=True)
    args = parser.parse_args()
    source_root = Path(__file__).resolve().parents[1]
    approval = load_approval(args.approval, args.approval_hash,
                             source_root=source_root)
    backend = Backend(approval, source_root,
                      validate_approval(approval, source_root=source_root))
    try:
        run(approval, backend, source_root=source_root)
    finally:
        backend.session.close()
    print("one direct DEV reader stage verified; TI acceptance remains separate",
          flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("direct reader operator failed:" + type(error).__name__
              + ";STOP_NO_RETRY", file=sys.stderr)
        raise SystemExit(1) from None
