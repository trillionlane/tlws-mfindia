#!/usr/bin/env bash
set -euo pipefail

# One fixed DEV read contract. No caller-controlled allowlist, new repository
# variables, public grants, writer IAM, secret access, or IAM revocation.
if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 preflight|required|reconcile" >&2
  exit 2
fi
mode="$1"
case "$mode" in preflight|required|reconcile) ;; *) exit 2 ;; esac
readonly project=trillionlane-dev region=asia-south1 service=tlws-mf-data-dev
readonly runtime=tlws-mf-data-runtime-dev@trillionlane-dev.iam.gserviceaccount.com
readonly approved_invokers=(
  "serviceAccount:$runtime"
  "serviceAccount:trillion-insights-runtime-dev@trillionlane-dev.iam.gserviceaccount.com"
  "serviceAccount:tl-dwf-dev-runtime@trillionlane-dev.iam.gserviceaccount.com"
)
allowed="$(printf '%s\n' "${approved_invokers[@]}" | jq -Rsc 'split("\n")[:-1]')"

# Only a successful exact-name list proves absence. Lookup/auth/API failures
# never permit a deployment to continue as if the provider did not exist.
services="$(gcloud run services list --project="$project" --region="$region" \
  --filter="metadata.name=$service" --format=json)"
count="$(jq -er --arg name "$service" '
  if type != "array" then error("invalid service list")
  else [.[] | select(.metadata.name == $name)] | length end
' <<<"$services")"
if [[ "$count" == 0 && "$mode" == preflight ]]; then
  echo "Private read service confirmed absent; first deployment may provision it"
  exit 0
fi
if [[ "$count" != 1 ]]; then
  echo "Expected exactly one private read service; found $count" >&2
  exit 1
fi
service_json="$(gcloud run services describe "$service" --project="$project" \
  --region="$region" --format=json)"
jq -e --arg runtime "$runtime" '
  .metadata.annotations["run.googleapis.com/ingress"] == "internal" and
  ((.metadata.annotations["run.googleapis.com/invoker-iam-disabled"] // "false") == "false") and
  .spec.template.spec.serviceAccountName == $runtime
' <<<"$service_json" >/dev/null

check_policy() {
  local required="$1" policy
  policy="$(gcloud run services get-iam-policy "$service" --project="$project" \
    --region="$region" --format=json)"
  jq -e --argjson allowed "$allowed" --arg required "$required" '
    if type != "object" then error("invalid IAM policy") else . end
    | (if has("bindings") then .bindings else [] end) as $bindings
    | if ($bindings | type) != "array" then error("invalid IAM bindings") else . end
    | [$bindings[] | select(.role == "roles/run.invoker")] as $invokers
    | if any($invokers[]; has("condition") or
        ((.members | type) != "array") or (.members | length == 0))
      then error("read invokers must have unconditional, nonempty member arrays") else . end
    | [$invokers[].members[]] as $members
    | if any($members[]; type != "string") then error("invalid IAM member") else . end
    | ($members - $allowed | length == 0) and
      ($members | length == (unique | length)) and
      ($required != "true" or ($members | sort) == ($allowed | sort))
  ' <<<"$policy" >/dev/null || {
    echo "Private read IAM differs: require only approved unconditional callers (complete=$required)" >&2
    return 1
  }
}

if [[ "$mode" == required ]]; then
  check_policy true
else
  # A known subset can be reconciled; an unknown/conditional/public grant
  # requires an explicit decision, never an automatic revocation.
  check_policy false
fi
if [[ "$mode" == reconcile ]]; then
  for member in "${approved_invokers[@]}"; do
    gcloud run services add-iam-policy-binding "$service" --project="$project" \
      --region="$region" --member="$member" --role=roles/run.invoker \
      --condition=None --quiet >/dev/null
  done
  check_policy true
fi
echo "Private read invoker contract verified ($mode); only MFData, Insights and TDW are approved"
