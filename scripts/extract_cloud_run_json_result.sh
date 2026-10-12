#!/usr/bin/env bash
set -euo pipefail

# Read `gcloud logging read --format=json` from stdin and emit the last
# application JSON object written to Cloud Run stdout. Cloud Run promotes a
# one-line JSON object to jsonPayload; textPayload is retained as a fallback
# for local/mocked log transports and older ingestion behaviour.
jq -cS '
  [
    .[]
    | select((.logName? // "") | endswith("%2Fstdout"))
    | if (.jsonPayload? | type) == "object" then
        .jsonPayload
      elif (.textPayload? | type) == "string" then
        (.textPayload | fromjson? | select(type == "object"))
      else
        empty
      end
  ]
  | last // empty
'
