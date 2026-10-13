#!/usr/bin/env bash
# Validate an apply receipt, including an exact-current zero-write apply.
# This is NOT certification: the subsequent full read-only scan stays required.
set -euo pipefail
jq -e '
  .methodology_version == "1" and
  (.dataset_version | type == "number" and . > 0 and . == floor) and
  (.schemes_assessed | type == "number" and . > 0 and . == floor) and
  (.nav_rows_scanned | type == "number" and . > 0 and . == floor) and
  (.findings_sha256 | type == "string" and test("^[0-9a-f]{64}$")) and
  (
    (.status == "NAV_INTEGRITY_AUDIT_OK" and
     .assessments_written == .schemes_assessed) or
    (.status == "NAV_INTEGRITY_AUDIT_ALREADY_CURRENT" and
     .assessments_written == 0)
  )
' >/dev/null
