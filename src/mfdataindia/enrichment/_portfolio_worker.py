"""Resource-limited original XLS decoder; no network, DB, macros or file writes."""

import json
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    from mfdataindia.enrichment.portfolios import MAX_BYTES, read_archive

    content = sys.stdin.buffer.read(MAX_BYTES + 1)
    if not 0 < len(content) <= MAX_BYTES:
        raise ValueError("source bound exceeded")
    sys.stdout.write(json.dumps(read_archive(content, sys.argv[1:])))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
