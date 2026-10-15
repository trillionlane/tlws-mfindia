#!/usr/bin/env python3
"""Offline MF-E09 source discovery. Exit 2 means publication remains BLOCKED."""
import argparse
import json
import os
import stat
from pathlib import Path

from mfdataindia.enrichment.idcw_discovery import MAX_BYTES, IdcwDiscoveryRequest, discover


def run(manifest, source_dir, output):
    if manifest.stat().st_size > 65536:
        raise ValueError("bounded request required")
    request = IdcwDiscoveryRequest.model_validate_json(manifest.read_bytes())
    root = source_dir.resolve(strict=True)
    sources = {}
    for role in ("history", "notice", "registry"):
        path = root / getattr(request, role).file
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
            raise ValueError("bounded regular source files required; no links")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
                raise ValueError("bounded regular source required")
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError("source grew beyond bound")
        sources[role] = content
    report = discover(request, sources)
    # Exclusive create: replay writes a new evidence file, never replaces one.
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(args.manifest, args.source_dir, args.output)
    except (ValueError, OSError):
        parser.exit(1, "IDCW discovery failed; no accepted evidence published\n")


if __name__ == "__main__":
    raise SystemExit(main())
