#!/usr/bin/env python3
"""Decode bounded certification input and run unchanged complete certification.

Context arguments must be pinned in the reviewed immutable worker invocation.
There is no raw-mode fallback, input logging or environment mutation.
"""

import argparse
import json
import os
import sys

from mfdataindia.review.certification_input_transport import PHASES, certify_from_environment
from mfdataindia.review.rollout import Manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=tuple(PHASES), required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--manifest-hash", required=True)
    args = parser.parse_args()
    try:
        manifest = Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"])
        if manifest.seal != args.manifest_hash:
            raise ValueError("worker manifest differs")
        result = certify_from_environment(manifest, os.environ, phase=args.phase,
                                          execution_id=args.execution_id)
        print(json.dumps(result, sort_keys=True), flush=True)
    except Exception as error:
        print("AMC book certification failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
