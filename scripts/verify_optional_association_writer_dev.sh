#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 3 ]]; then
  echo "usage: $0 PROJECT REGION SERVICE" >&2
  exit 2
fi

project="$1"
region="$2"
service="$3"

# A successful exact-name list is the absence proof. Do not treat a failed
# describe (permission denial, API outage, auth expiry) as if the writer does
# not exist.
services_json="$(gcloud run services list \
  --project="$project" \
  --region="$region" \
  --filter="metadata.name=$service" \
  --format=json)"
service_count="$(jq -er --arg service "$service" '
  [.[] | select(.metadata.name == $service)] | length
' <<<"$services_json")"
case "$service_count" in
  0)
    echo "Optional association writer is confirmed absent"
    exit 0
    ;;
  1) ;;
  *)
    echo "Expected at most one association writer, found $service_count" >&2
    exit 1
    ;;
esac

writer_json="$(gcloud run services describe "$service" \
  --project="$project" \
  --region="$region" \
  --format=json)"

template_enabled="$(jq -er '
  [.spec.template.spec.containers[0].env[]?
    | select(.name == "MFDATAINDIA_ASSOCIATION_WRITES_ENABLED")
    | .value]
  | if length == 1 then .[0] else error("writer template flag is missing or duplicated") end
' <<<"$writer_json")"
if [[ "$template_enabled" != "false" ]]; then
  echo "Association writer latest template is not disabled" >&2
  exit 1
fi

reachable_revisions="$(jq -er '
  .status.latestReadyRevisionName as $latest
  | [.status.traffic[]?
      | select(
          ((.percent // 0) > 0) or
          (((.tag // "") | length) > 0)
        )
      | if ((.revisionName // "") | length) > 0 then .revisionName
        elif .latestRevision == true and (($latest // "") | length) > 0 then $latest
        else error("unable to resolve a traffic-serving or tagged writer revision")
        end]
  | unique
  | if length > 0 then join("\n") else error("writer has no reachable revision") end
' <<<"$writer_json")"

checked=0
while IFS= read -r revision; do
  [[ -n "$revision" ]] || continue
  revision_json="$(gcloud run revisions describe "$revision" \
    --project="$project" \
    --region="$region" \
    --format=json)"
  revision_enabled="$(jq -er '
    [.spec.containers[0].env[]?
      | select(.name == "MFDATAINDIA_ASSOCIATION_WRITES_ENABLED")
      | .value]
    | if length == 1 then .[0] else error("writer revision flag is missing or duplicated") end
  ' <<<"$revision_json")"
  if [[ "$revision_enabled" != "false" ]]; then
    echo "Association writer reachable revision $revision is not disabled" >&2
    exit 1
  fi
  checked=$((checked + 1))
done <<<"$reachable_revisions"

if [[ "$checked" -lt 1 ]]; then
  echo "Association writer has no verified reachable revision" >&2
  exit 1
fi

echo "Optional association writer is disabled on its template and $checked reachable revision(s)"
