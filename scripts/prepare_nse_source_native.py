#!/usr/bin/env python3
"""Prepare a private SCH44 source-native candidate. File-only, never publishes."""

import argparse
import os
from pathlib import Path

from mfdataindia.nse import contract as c, sch44, source_native


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "source",
        "provenance",
        "catalogue",
        "earlier-source",
        "amfi-first",
        "amfi-second",
        "product-review",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--product-review-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:

        def read(path):
            return sch44._read(path, c.MAX_BYTES)

        candidate = source_native.prepare(
            read(args.source),
            c.decode(read(args.provenance)),
            read(args.catalogue),
            read(args.earlier_source),
            read(args.amfi_first),
            read(args.amfi_second),
            c.decode(read(args.product_review)),
            args.product_review_sha256,
        )
        body = c.canonical(candidate)
        # Creation is deliberately exclusive. Never replace a reviewed candidate.
        with args.output.open("xb") as handle:
            os.chmod(args.output, 0o600)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, c.ImportError) as error:
        reason = (
            str(error)
            if isinstance(error, c.ImportError)
            else "PRIVATE_INPUT_OR_OUTPUT_UNAVAILABLE"
        )
        parser.exit(2, "NSE_SOURCE_PREPARATION: " + reason + "\n")
    print(c.digest(candidate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
