#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 6 ]]; then
  echo "usage: $0 SERVICE PROJECT REGION DISABLED_REVISION ENABLED_REVISION GIT_SHA" >&2
  exit 2
fi

service_name="$1"
project_id="$2"
region="$3"
disabled_revision="$4"
enabled_revision="$5"
deployed_sha="$6"

promoted=false
activation_succeeded=false

rollback_on_exit() {
  local rc=$?
  trap - EXIT
  if [[ "$activation_succeeded" != "true" && "$promoted" == "true" ]]; then
    echo "Activation failed; restoring disabled revision $disabled_revision" >&2
    gcloud run services update-traffic "$service_name" \
      --project="$project_id" \
      --region="$region" \
      --to-revisions="$disabled_revision=100" \
      --quiet || true
  fi
  exit "$rc"
}
trap rollback_on_exit EXIT

# Set this before the command: even a non-zero CLI result may follow a partial
# traffic update, so every unsuccessful exit from this point must roll back.
promoted=true
gcloud run services update-traffic "$service_name" \
  --project="$project_id" \
  --region="$region" \
  --to-revisions="$enabled_revision=100" \
  --quiet

after="$(gcloud run services describe "$service_name" \
  --project="$project_id" \
  --region="$region" \
  --format=json)"
jq -e --arg revision "$enabled_revision" \
  '.status.traffic | length == 1 and .[0].revisionName == $revision and .[0].percent == 100' \
  <<<"$after" >/dev/null
jq -e '.metadata.annotations["run.googleapis.com/ingress"] == "internal"' \
  <<<"$after" >/dev/null

service_url="$(jq -er '.status.url' <<<"$after")"
external_status="$(curl --silent --show-error --max-time 20 \
  --output /dev/null --write-out '%{http_code}' "$service_url/api/health" || true)"
case "$external_status" in
  2*)
    echo "Association writer unexpectedly reachable from public runner: HTTP $external_status" >&2
    exit 1
    ;;
esac

activation_succeeded=true
promoted=false
trap - EXIT
echo "Association writes enabled on revision $enabled_revision for deployed SHA $deployed_sha"
