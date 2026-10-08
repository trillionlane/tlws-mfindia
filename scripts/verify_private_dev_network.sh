#!/usr/bin/env bash
set -euo pipefail

# Read-only preflight for the private DEV smoke path. Direct VPC egress uses
# the existing Cloud Run service agent; this script neither grants IAM nor
# deploys, invokes, migrates, or changes any resource.

PROJECT_ID="${PROJECT_ID:-trillionlane-dev}"
REGION="${REGION:-asia-south1}"
NETWORK="${NETWORK:-tl-dev-vpc}"
SUBNET="${SUBNET:-tl-dev-mumbai}"
INSIGHTS_RUNTIME_SA="trillion-insights-runtime-dev@$PROJECT_ID.iam.gserviceaccount.com"

test "$PROJECT_ID" = "trillionlane-dev"
test "$REGION" = "asia-south1"
test "$NETWORK" = "tl-dev-vpc"
test "$SUBNET" = "tl-dev-mumbai"
test "$(gcloud config get-value project 2>/dev/null)" = "$PROJECT_ID"

project_number="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
service_agent="service-$project_number@serverless-robot-prod.iam.gserviceaccount.com"

gcloud iam service-accounts describe "$INSIGHTS_RUNTIME_SA" --project="$PROJECT_ID" >/dev/null
gcloud compute networks describe "$NETWORK" --project="$PROJECT_ID" >/dev/null
subnet_json="$(gcloud compute networks subnets describe "$SUBNET" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --format=json)"
jq -e --arg network "$NETWORK" \
  '.network | endswith("/networks/" + $network)' <<<"$subnet_json" >/dev/null
jq -e '.privateIpGoogleAccess == true' <<<"$subnet_json" >/dev/null

project_policy="$(gcloud projects get-iam-policy "$PROJECT_ID" --format=json)"
jq -e \
  --arg member "serviceAccount:$service_agent" \
  '.bindings[] | select(.role == "roles/run.serviceAgent") | .members | index($member) != null' \
  <<<"$project_policy" >/dev/null

echo "MFDataIndia private DEV network preflight passed"
