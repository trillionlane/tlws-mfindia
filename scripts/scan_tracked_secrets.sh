#!/usr/bin/env bash
# Scan tracked source for high-confidence credential markers.

set -euo pipefail

pattern='-----BEGIN ([A-Z0-9 ]+ )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|AKIA[0-9A-Z]{16}'

if git grep -nEI -e "$pattern" -- . ':!scripts/scan_tracked_secrets.sh'; then
  echo "Tracked credential-like material detected." >&2
  exit 1
fi

echo "No high-confidence credential markers found in tracked files."
