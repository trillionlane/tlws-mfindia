#!/usr/bin/env bash
set -euo pipefail

# One-time, idempotent bootstrap for the governed MFDataIndia DEV foundation.
# This script deliberately does not restore data, deploy Cloud Run, or create a
# scheduler. Those are separate evidence-gated workflows.

PROJECT_ID="${PROJECT_ID:-trillionlane-dev}"
REGION="${REGION:-asia-south1}"
REPOSITORY="${REPOSITORY:-tlws-mf-data-dev}"
SQL_INSTANCE="${SQL_INSTANCE:-tlws-mf-data-dev}"
DATABASE="${DATABASE:-mfdataindia}"
WIF_POOL="${WIF_POOL:-github-pool}"
WIF_PROVIDER="${WIF_PROVIDER:-tlws-mf-data-github}"
GITHUB_REPOSITORY="trillionlane/tlws-mfindia"
GITHUB_REPOSITORY_ID="1403671867"

test "$PROJECT_ID" = "trillionlane-dev"
test "$REGION" = "asia-south1"
test "$(gcloud config get-value project 2>/dev/null)" = "$PROJECT_ID"

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
CONNECTION_NAME="$PROJECT_ID:$REGION:$SQL_INSTANCE"

DEPLOY_SA="tlws-mf-data-deploy-dev@$PROJECT_ID.iam.gserviceaccount.com"
RUNTIME_SA="tlws-mf-data-runtime-dev@$PROJECT_ID.iam.gserviceaccount.com"
MIGRATE_SA="tlws-mf-data-migrate-dev@$PROJECT_ID.iam.gserviceaccount.com"
RESTORE_SA="tlws-mf-data-restore-dev@$PROJECT_ID.iam.gserviceaccount.com"
INGEST_SA="tlws-mf-data-ingest-dev@$PROJECT_ID.iam.gserviceaccount.com"
SCHEDULER_SA="tlws-mf-data-scheduler-dev@$PROJECT_ID.iam.gserviceaccount.com"

ensure_service_account() {
  local account_id="$1"
  local display_name="$2"
  local email="$account_id@$PROJECT_ID.iam.gserviceaccount.com"
  if ! gcloud iam service-accounts describe "$email" --project="$PROJECT_ID" >/dev/null 2>&1; then
    local attempt output
    for attempt in 1 2 3; do
      if output="$(gcloud iam service-accounts create "$account_id" \
        --project="$PROJECT_ID" \
        --display-name="$display_name" 2>&1)"; then
        printf '%s\n' "$output"
        return
      fi
      printf '%s\n' "$output" >&2
      if [[ "$output" != *"RESOURCE_EXHAUSTED"* || "$attempt" -eq 3 ]]; then
        return 1
      fi
      echo "Service-account creation quota reached; retrying after 60 seconds" >&2
      sleep 60
    done
  fi
}

ensure_project_role() {
  local member="$1"
  local role="$2"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="$member" \
    --role="$role" \
    --condition=None \
    --quiet >/dev/null
}

ensure_secret_access() {
  local secret="$1"
  local service_account="$2"
  gcloud secrets add-iam-policy-binding "$secret" \
    --project="$PROJECT_ID" \
    --member="serviceAccount:$service_account" \
    --role=roles/secretmanager.secretAccessor \
    --condition=None \
    --quiet >/dev/null
}

ensure_act_as() {
  local service_account="$1"
  gcloud iam service-accounts add-iam-policy-binding "$service_account" \
    --project="$PROJECT_ID" \
    --member="serviceAccount:$DEPLOY_SA" \
    --role=roles/iam.serviceAccountUser \
    --condition=None \
    --quiet >/dev/null
}

ensure_service_account tlws-mf-data-deploy-dev "MFDataIndia DEV deployer"
ensure_service_account tlws-mf-data-runtime-dev "MFDataIndia DEV read-only runtime"
ensure_service_account tlws-mf-data-migrate-dev "MFDataIndia DEV migration runner"
ensure_service_account tlws-mf-data-restore-dev "MFDataIndia DEV snapshot restore runner"
ensure_service_account tlws-mf-data-ingest-dev "MFDataIndia DEV ingestion runner"
ensure_service_account tlws-mf-data-scheduler-dev "MFDataIndia DEV scheduler invoker"

if ! gcloud artifacts repositories describe "$REPOSITORY" \
  --project="$PROJECT_ID" --location="$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPOSITORY" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --repository-format=docker \
    --description="Immutable MFDataIndia DEV images"
fi

gcloud artifacts repositories add-iam-policy-binding "$REPOSITORY" \
  --project="$PROJECT_ID" \
  --location="$REGION" \
  --member="serviceAccount:$DEPLOY_SA" \
  --role=roles/artifactregistry.writer \
  --condition=None \
  --quiet >/dev/null

ensure_project_role "serviceAccount:$DEPLOY_SA" roles/run.admin
ensure_project_role "serviceAccount:$DEPLOY_SA" roles/cloudsql.viewer
for account in "$RUNTIME_SA" "$MIGRATE_SA" "$RESTORE_SA" "$INGEST_SA"; do
  ensure_project_role "serviceAccount:$account" roles/cloudsql.client
  ensure_act_as "$account"
done

if ! gcloud iam workload-identity-pools providers describe "$WIF_PROVIDER" \
  --project="$PROJECT_ID" \
  --location=global \
  --workload-identity-pool="$WIF_POOL" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" \
    --project="$PROJECT_ID" \
    --location=global \
    --workload-identity-pool="$WIF_POOL" \
    --display-name="MFDataIndia GitHub main" \
    --issuer-uri=https://token.actions.githubusercontent.com \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref,attribute.actor=assertion.actor" \
    --attribute-condition="assertion.repository == '$GITHUB_REPOSITORY' && assertion.repository_id == '$GITHUB_REPOSITORY_ID' && assertion.ref == 'refs/heads/main'"
fi

WIF_MEMBER="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$WIF_POOL/attribute.repository/$GITHUB_REPOSITORY"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --project="$PROJECT_ID" \
  --member="$WIF_MEMBER" \
  --role=roles/iam.workloadIdentityUser \
  --condition=None \
  --quiet >/dev/null

if ! gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud sql instances create "$SQL_INSTANCE" \
    --project="$PROJECT_ID" \
    --database-version=POSTGRES_18 \
    --edition=enterprise \
    --region="$REGION" \
    --tier=db-g1-small \
    --availability-type=zonal \
    --storage-type=SSD \
    --storage-size=10 \
    --storage-auto-increase \
    --backup-start-time=00:30 \
    --enable-point-in-time-recovery \
    --retained-backups-count=7 \
    --maintenance-window-day=SUN \
    --maintenance-window-hour=2 \
    --database-flags=cloudsql.iam_authentication=on \
    --deletion-protection
else
  test "$(gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" --format='value(databaseVersion)')" = "POSTGRES_18"
  test "$(gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" --format='value(region)')" = "$REGION"
fi

if ! gcloud sql databases describe "$DATABASE" \
  --instance="$SQL_INSTANCE" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud sql databases create "$DATABASE" \
    --instance="$SQL_INSTANCE" \
    --project="$PROJECT_ID" \
    --charset=UTF8
fi

create_database_principal() {
  local database_user="$1"
  local secret_name="$2"
  local users
  users="$(gcloud sql users list --instance="$SQL_INSTANCE" --project="$PROJECT_ID" --format='value(name)')"
  if printf '%s\n' "$users" | grep -Fxq "$database_user"; then
    gcloud secrets describe "$secret_name" --project="$PROJECT_ID" >/dev/null 2>&1 || {
      echo "Database user $database_user exists but secret $secret_name is absent; refusing password rotation" >&2
      return 1
    }
    return
  fi

  if gcloud secrets describe "$secret_name" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Secret $secret_name exists but database user $database_user is absent; refusing ambiguous recovery" >&2
    return 1
  fi

  local password dsn
  password="$(openssl rand -hex 32)"
  gcloud sql users create "$database_user" \
    --instance="$SQL_INSTANCE" \
    --project="$PROJECT_ID" \
    --password="$password"
  gcloud secrets create "$secret_name" \
    --project="$PROJECT_ID" \
    --replication-policy=automatic \
    --labels=product=mfdataindia,environment=dev
  dsn="postgresql://$database_user:$password@/$DATABASE?host=/cloudsql/$CONNECTION_NAME"
  printf '%s' "$dsn" | gcloud secrets versions add "$secret_name" \
    --project="$PROJECT_ID" \
    --data-file=- >/dev/null
  unset password dsn
}

create_database_principal mfdata_app tlws-mf-data-runtime-dsn-dev
create_database_principal mfdata_admin tlws-mf-data-admin-dsn-dev
create_database_principal mfdata_ingest tlws-mf-data-ingest-dsn-dev

ensure_secret_access tlws-mf-data-runtime-dsn-dev "$RUNTIME_SA"
ensure_secret_access tlws-mf-data-admin-dsn-dev "$MIGRATE_SA"
ensure_secret_access tlws-mf-data-admin-dsn-dev "$RESTORE_SA"
ensure_secret_access tlws-mf-data-ingest-dsn-dev "$INGEST_SA"

gcloud storage buckets add-iam-policy-binding gs://tlws_mf_data_source \
  --member="serviceAccount:$RESTORE_SA" \
  --role=roles/storage.objectViewer \
  --condition="expression=resource.name == 'projects/_/buckets/tlws_mf_data_source/objects/mfdataindia_full_20261008_045418_snapshot.dump',title=mfdataindia-approved-snapshot,description=Read only the approved MFDataIndia snapshot" \
  --quiet >/dev/null

echo "MFDataIndia DEV foundation is ready."
echo "Workload identity provider: projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$WIF_POOL/providers/$WIF_PROVIDER"
echo "Cloud SQL connection: $CONNECTION_NAME"
