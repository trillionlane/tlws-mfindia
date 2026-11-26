"""Export/check native producer contracts, local evidence and future alignment notes."""

import argparse
from pathlib import Path

from mfdataindia.producer_contract.compatibility import generated_files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = generated_files(root)
    if args.check:
        return int(any(not (root / path).is_file() or (root / path).read_bytes() != raw
                       for path, raw in files.items()))
    for path, raw in files.items():
        (root / path).write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
