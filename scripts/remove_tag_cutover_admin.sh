#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${1:-trillionlane-dev}"
DEPLOY_SERVICE_ACCOUNT="${2:-tlws-mf-data-deploy-dev@$PROJECT_ID.iam.gserviceaccount.com}"
SECRET="tlws-mf-data-tag-writer-dsn-dev"
member="serviceAccount:$DEPLOY_SERVICE_ACCOUNT"

test "$PROJECT_ID" = "trillionlane-dev"
test "$DEPLOY_SERVICE_ACCOUNT" = "tlws-mf-data-deploy-dev@$PROJECT_ID.iam.gserviceaccount.com"

policy="$(gcloud secrets get-iam-policy "$SECRET" --project="$PROJECT_ID" --format=json)"
if jq -e --arg member "$member" '
  any(.bindings[]? | select(.role == "roles/secretmanager.admin");
      any(.members[]?; . == $member))
' <<<"$policy" >/dev/null; then
  policy_file="$(mktemp)"
  trap 'rm -f "$policy_file"' EXIT
  jq --arg member "$member" '
    .bindings |= [
      .[]
      | if .role == "roles/secretmanager.admin"
        then .members = [.members[]? | select(. != $member)]
        else . end
      | select((.members | length) > 0)
    ]
  ' <<<"$policy" >"$policy_file"
  gcloud secrets set-iam-policy "$SECRET" "$policy_file" \
    --project="$PROJECT_ID" --quiet >/dev/null
fi

policy="$(gcloud secrets get-iam-policy "$SECRET" --project="$PROJECT_ID" --format=json)"
jq -e --arg member "$member" '
  all(.bindings[]? | select(.role == "roles/secretmanager.admin");
      all(.members[]?; . != $member))
' <<<"$policy" >/dev/null
