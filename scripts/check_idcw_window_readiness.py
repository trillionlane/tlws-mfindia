#!/usr/bin/env python3
"""Read original sources offline. Exit 2 is a blocked diagnostic, NEVER readiness PASS."""

import argparse
import csv
import json
import os
import stat
from pathlib import Path

from mfdataindia.enrichment.idcw_readiness import (
    LIMITS,
    ROLES,
    ReadinessRequest,
    assess,
    strict_json,
)


def read_regular(path, maximum):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
        raise ValueError("bounded regular input required; no links")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as handle:
        actual = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(actual.st_mode)
            or (actual.st_dev, actual.st_ino) != (before.st_dev, before.st_ino)
            or not 0 < actual.st_size <= maximum
        ):
            raise ValueError("input changed or exceeds bounds")
        raw = handle.read(maximum + 1)
    if len(raw) != actual.st_size or len(raw) > maximum:
        raise ValueError("input changed while reading")
    return raw


def run(manifest, source_dir, output):
    request = ReadinessRequest.model_validate(strict_json(read_regular(manifest, 65536)))
    root = source_dir.resolve(strict=True)
    info = root.stat()
    if not root.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("task-owned private source directory required")
    sources = {
        role: read_regular(root / getattr(request, role).file, LIMITS[role]) for role in ROLES
    }
    report = assess(request, sources)
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(report, handle, sort_keys=True, indent=2)
        handle.write("\n")
    return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(args.manifest, args.source_dir, args.output)
    except (ValueError, OSError, csv.Error):
        parser.exit(1, "IDCW window source validation failed; no readiness accepted\n")


if __name__ == "__main__":
    raise SystemExit(main())
