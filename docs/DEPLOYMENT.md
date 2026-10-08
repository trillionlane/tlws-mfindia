# MFDataIndia deployment contract

Status: DEV implementation and deployment approved on 2026-10-08. Execution
remains sequential and evidence-gated: repository verification and merge,
infrastructure provisioning, snapshot restore, service deployment, supervised
refresh, and scheduler activation are separate checkpoints. Production remains
out of scope.

## Approved DEV target

| Item | Contract |
|---|---|
| GitHub repository | `trillionlane/tlws-mfindia` (repository ID `1403671867`) |
| Branch admitted by WIF | `refs/heads/main` only |
| GCP project | `trillionlane-dev` |
| GCP region | `asia-south1` |
| Database engine | Dedicated Cloud SQL for PostgreSQL 18 |
| Application | Private, read-only Cloud Run API in DEV; internal ingress only |
| API consumer | Trillion Insights backend through VPC egress and Google-signed OIDC |
| Browser access | No direct MFDataIndia access; the Insights UI uses its own backend |
| Production | Out of scope until the production gate is approved |

Provisional resource names are `tlws-mf-data-dev` for the Cloud Run service,
`tlws-mf-data-nav-refresh-dev` for the scheduled job, and
`tlws-mf-data-restore-dev` for the one-shot restore job. Provisioning must first
confirm that none of the names already exists.

## Snapshot baseline

The approved base snapshot is immutable by object generation, not just by name:

| Property | Value |
|---|---|
| Object | `gs://tlws_mf_data_source/mfdataindia_full_20261008_045418_snapshot.dump` |
| Generation | `1791431731173325` |
| Size | `47,801,979` bytes |
| MD5 | `HYE6KQY48HZYqwtA8+w7Cw==` |
| CRC32C | `GFcdHg==` |
| Format | PostgreSQL custom archive `1.16-0`, gzip |
| Source server and tool | PostgreSQL `18.6` |
| Archive database | `mfdataindia` |

The existing `tl-dev-postgres` instance is PostgreSQL 16 and is not an approved
restore target for this PostgreSQL 18 archive. The restore must use a PostgreSQL
18 client and target, verify the exact object generation and checksums before
opening the archive, omit source ownership and ACLs, and refuse a non-empty
target database.

The archive includes schema and data through migrations `001`–`013`, including
`mf.fund_family`. It is the deployment baseline. A restore workflow must verify
that schema before recording the baseline; it must never replay migrations
`001`–`013` against the restored database. Future forward migrations start at
`014` and run through a version ledger.

## Delivery workflows

The repository keeps these operations separate:

1. `verify.yml` runs lint, the complete unit and PostgreSQL 18 integration suite,
   dependency audit, credential-pattern scan, and production-image build. It has
   no cloud credentials.
2. `deploy-dev.yml` authenticates with GitHub OIDC, validates the exact project,
   region, deployer, repository and branch, publishes one SHA-tagged image, runs
   only pending forward migrations with `maxRetries=0`, deploys Cloud Run with
   internal ingress and authentication required, reconciles its exact invoker
   policy, and verifies the deployed digest and source SHA. Functional smoke
   requests run from a zero-retry Cloud Run job attached to the approved VPC;
   the public GitHub runner must not reach the service. Automatic deployment remains
   disabled unless `MFDATAINDIA_DEV_AUTO_DEPLOY=true`; supervised manual dispatch
   is the first-deployment path.
3. `restore-dev-snapshot.yml` is manual-only and requires typed project,
   database, object and generation confirmation. It verifies remote and local
   object checksums, refuses a non-empty database, uses PostgreSQL 18 tooling and
   a dedicated restore identity, validates the restored data manifest, records
   the verified migration baseline, and runs with `maxRetries=0`.
4. `refresh-nav-dev.yml` deploys the exact live image as a zero-retry ingestion
   job and performs one supervised refresh. `enable-nav-schedule-dev.yml` refuses
   activation until that execution succeeded, then creates the temporary
   six-hour `Asia/Kolkata` schedule with a dedicated invoker identity.

## Identity boundaries

- The GitHub deploy identity may publish images and update the declared DEV
  service/jobs, but cannot read the database password.
- The runtime identity may connect to only the MFDataIndia database and read only
  its runtime DSN secret. It may invoke the MFDataIndia service only to run the
  bounded private deployment smoke.
- The Trillion Insights runtime identity may invoke the MFDataIndia service. It
  receives no database, migration, ingestion or secret access from this project.
- No `allUsers` Cloud Run invoker binding is permitted. MFDataIndia uses internal
  ingress, and callers authenticate with short-lived Google-signed OIDC tokens.
- The migration identity owns schema changes but is not used by the API.
- The restore identity may read only the approved snapshot bucket/object and the
  restore DSN secret.
- The scheduler identity may invoke only the NAV refresh job.
- No user-managed service-account keys are permitted.

## Deployment gates

Repository preparation is not deployment. Image publication, database creation,
snapshot restore, Cloud Run revision creation, scheduler activation, production
promotion and certification are independently reviewable gates.

Before the first DEV deployment, evidence must include:

- all CI checks passing against PostgreSQL 18;
- exact snapshot generation and checksum verification;
- restored table/partition counts and NAV-date range manifest;
- non-empty, orphan-free `mf.fund_family` coverage;
- zero active MFAPI or Scripbox configuration;
- internal Cloud Run health, stats, search, fund detail and NAV smoke tests;
- external access rejected, exact private invoker policy, and no API key or
  long-lived client/service-account secret;
- deployed image digest and `DEPLOYMENT_GIT_SHA` reconciliation;
- a documented database backup and revision rollback path.

Scheduling is a later gate. During the initial DEV observation period, the job
runs every six hours in `Asia/Kolkata` with `maxRetries=0`; valid unchanged-feed
executions are safe zero-row outcomes. The intended steady-state cadence remains
19:30 `Asia/Kolkata`, Monday–Friday, and requires a separately reviewed schedule
change.
