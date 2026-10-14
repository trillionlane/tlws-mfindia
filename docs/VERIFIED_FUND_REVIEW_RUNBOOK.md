# Verified fund review API

This provider feature combines ENH-01, the initial ANA-01 analytics slice and
DATA-05 review APIs. It adds policy-bound analytics and durable human review
without changing existing NAVs, identities, facts, calculations, lifecycle or
quality decisions. The existing 19-route v2 API and association contract stay
unchanged. Insights UI, source corrections and automatic generation are excluded.

## Data preservation and storage

The optional migration `sql/optional/023_verified_fund_review.sql` creates an
empty `mf_review` schema with append-only policies, input snapshots, results and
review/proposal events. It is outside `DEFAULT_MIGRATIONS` and the initdb glob;
ordinary migration/deployment does not install or populate it. The new generator
never calls the legacy calculator, auditor, dataset-summary updater or ingester.
Insert triggers verify canonical UTF-8 content hashes at the database boundary;
immutable triggers reject updates, deletes and truncation. Mathematical execution
is the trusted, policy-validated generator's responsibility, not arbitrary SQL.

Each snapshot retains exact NAV decimal text, dates, selected source identity and
facts, source revision, dataset version and lifecycle/freshness/quality context.
Results identify the policy hash, input hash, algorithm source hash, engine version,
Python version and decimal-library version. Financial results have no run timestamp
or live-rate input. Observation sets are bounded to 30,000 points per fund; an
explicit generation batch is limited to 25 unique positive fund codes.

Generation uses a repeatable-read read-only capture, then rechecks inputs in a
READ COMMITTED transaction under the existing NAV-operation lock before publishing
one atomic batch. Mutations reject repeatable-read transactions: a snapshot taken
before lock acquisition must not admit an intervening committed refresh. An internal
savepoint rolls back the batch even if a caller catches its exception. Replays
reuse immutable artifacts. Changed inputs create new results; old results remain
retrievable and are labelled stale when their exact captured dependencies differ.
GET requests never calculate or write results.

Source-write inventory: ordinary `upsert_nav` can update existing observations;
`upsert_funds` and enrichment loaders can update existing facts/identities;
legacy `recompute` upserts `nav_calculations`; audit publication updates quality
assessments; the normal migrator can invoke these derived publishers. These are
pre-existing paths, unchanged here. This feature does not certify their compliance
with the broader preservation roadmap or retroactively make the whole database
immutable. Its new principals have no source DML or privileged-source-function
access. The separately approved correction executor remains dormant.

## Stored mathematical policy

`src/mfdataindia/review/policy.py` is the source-controlled definition of
`growth-observed-nav`, version 1. The generator stores that exact document and
canonical SHA-256 hash. Registry documents cannot inject or alter executable
formulas: any unsupported/modified document is rejected. A new method requires
a new code/policy version, independent fixtures and separately retained results.

Seven outputs are available: cumulative return, ACT/365.25 CAGR, sample volatility,
Sharpe, Sortino, maximum drawdown and Calmar. Returns/drawdown/volatility are
percentages; ratios are dimensionless. Outputs are decimal strings at eight
decimal places, computed at precision 50 with half-even rounding independent of
ambient Decimal context. Sharpe/Sortino use simple periodic excess returns and
an effective daily conversion of the disclosed fixed 6.5% annual assumption.
This is not a live or historical Treasury-bill yield.

Risk annualization assumes 252 observed business-day periods. At least 253
observations are required; weekend observations or gaps over seven calendar days
make risk metrics unavailable. A verified holiday calendar is not supplied;
shorter gaps are an explicit observed-interval assumption, not evidence that
every trading session exists. No forward-fill or source adjustment is performed.
CAGR requires at least 365 elapsed days. Zero volatility, zero downside deviation,
zero drawdown, nonpositive/nonfinite NAV and unsupported options have explicit
null/reason behavior. IDCW/bonus/unknown are unsupported, never reconstructed.
Inactive, stale or signalled funds may retain arithmetic results for investigation,
but cannot establish current comparison eligibility. Consumers must check both
`comparison_eligible` and each metric's availability.

Audit and canonical publication of monthly/rolling methods remain a follow-up
slice; the new endpoint does
not silently redefine their existing UI/API outputs. This initial slice does not
complete every ANA-01 deliverable or establish whole-database calculation coverage.

## Optional principals and runtime checks

Provision identities only after explicit rollout approval, then run
`scripts/sql/reconcile_verified_review_privileges.sql` as migration administrator.
It requires, but never creates, three optional database principals:

- `mfdata_verified_generator`: declared source SELECT plus INSERT/SELECT on new
  policy/snapshot/result tables. No event writes or source DML.
- `mfdata_verified_reader`: declared source and new-record SELECT only.
- `mfdata_verified_admin`: those reads plus EXECUTE on the bounded event function,
  not direct INSERT/UPDATE/DELETE on the event ledger.

Reconciliation removes inherited memberships, CREATEDB/CREATEROLE when present,
table/column ACLs and CREATE privileges from these principals only. Superuser,
replication, bypass-RLS and relation ownership fail closed rather than demanding
superuser just to reiterate an already-safe negative attribute. Actual runtime checks
reject wrong/elevated identities, memberships, source table or column DML,
privileged source functions and API direct artifact writes. A direct dedicated
login is required; administrator credentials with SET ROLE are rejected.
Migration removes known pre-existing runtime default grants on the new schema.
Cloud SQL effective privilege verification remains a live rollout gate.

## Backend and admin authorization

The factory is `mfdataindia.review.app:create_review_app`. The existing production
entrypoint is unchanged. Without `MFDATAINDIA_REVIEW_TRUST_FILE`, the new app is
dormant: no startup connection and all feature requests return 503. A configured
app verifies runtime privileges before serving.

The single mounted trust JSON contains exactly `public_key` (Ed25519 public PEM),
`issuer`, `audience`, `caller` and `allowed_scopes`. Private signing material belongs
only to the trusted consumer's authenticated server-side component; never to a
browser, this repository or the provider. Trust provisioning/key ownership is
separate consumer/rollout work, not a generated secret or a per-gate GitHub flag.

Requests carry a signed JWT with exactly `iss`, `aud`, `sub`, `caller`, `scope`,
`iat`, `nbf`, `exp` and `jti`. Audience/caller/issuer must match the pinned trust;
only EdDSA/Ed25519 is accepted, expiry is mandatory, lifetime is at most 300 seconds,
and granted scopes cannot exceed configured capabilities. `sub` is the authenticated
actor's stable identifier. The consumer must authenticate the actor and verify
admin entitlement before issuing `review:admin`; arbitrary actor/role headers or
body fields are rejected. Provider signing-key verification authenticates that
delegation, not the consumer's unimplemented login/RBAC system.

All reads require `review:read` or `review:admin`; mutations require `review:admin`.
Public access remains forbidden. For an eventual private Cloud Run deployment,
keep internal ingress and allowlisted invoker IAM. Use a Google ID token in
`X-Serverless-Authorization` and the delegated assertion in `Authorization`.
[Cloud Run service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)
supports this separation. JWT verification follows
[PyJWT's pinned-algorithm guidance](https://pyjwt.readthedocs.io/en/stable/api.html).

## Consumer contract

`contracts/mfdataindia-verified-review-v1.json` pins six endpoints. Every response,
including errors, has `Cache-Control: no-store`.

| Method and path under `/api/review/v1` | Purpose |
| --- | --- |
| GET `/policies/{policy_hash}` | Exact registered policy and version. |
| GET `/funds` | Keyset-paginated queue; status/quality-signal filters, maximum 100 rows. |
| GET `/funds/{code}` | Original identity/facts, current context, human status and paginated history. |
| GET `/funds/{code}/analytics` | Latest publication, or exact `result_id`; current/stale inputs and metric limitations. |
| POST `/funds/{code}/reviews` | Append a signed-actor-attributed status/disposition/evidence event. |
| POST `/funds/{code}/enrichment-proposals` | Append a non-executable metadata proposal; never apply an override. |

Both POSTs require a stable `Idempotency-Key`, `expected_version`,
`expected_input_hash`, `result_id`, bounded reason and evidence references.
Review statuses are pending_review, in_review, reviewed and needs_evidence.
Disposition is separate: not_determined, consistent_with_evidence,
discrepancy_candidate or insufficient_evidence. Reviewed status requires evidence
and a determined disposition; it never clears quality signals or grants eligibility.

The event function checks linear versions, replay identity/payload, fund/result
binding and current reviewed dependencies. Repeated identical requests return
the original event without another write, even if the fund subsequently changes;
this is historical success, not renewed approval. Changed keys/payloads/actors,
stale reviewed inputs, concurrent decisions and a busy NAV operation return 409.

Proposals use an explicit metadata field allowlist; derived metrics, NAVs and
identity keys cannot be submitted as editable fields. Original values are visible
in the input snapshot; each proposal records `execution: not_executable`.
Applying overlays, changing original fields, assignment workflows and bulk admin
editing are not provided. Reviews/proposals persist independently of sessions.

The event version advances for both reviews and proposals. Human status and
review revalidation depend only on the latest actual review decision and its
input snapshot, independently of history pagination. A proposal retains its own
result as evidence but cannot renew review freshness, even after fresh analytics
are published. Only a new review of the current inputs can clear revalidation.
With no actual review, status remains pending_review and there are no reviewed
dependencies to revalidate; this does not mean the fund has been validated.

Queue `needs_revalidation: true` proves detected dependency drift; null means
unchecked, never current/clean. Detail compares the full input fingerprint and
returns an exact boolean. Missing analytics returns 404, not on-demand generation.
Neither queue membership nor a published result certifies a fund's correctness.

## Explicit operator commands

No command below has been authorized for live execution by implementing this PR.
Use the existing DSN mechanism with the appropriately restricted identity; do not
pass secrets as command-line arguments or retain them in output.

```sh
uv run python scripts/verified_fund_review.py policy

# Separately approved administrator operation; installs empty structures only.
uv run python scripts/verified_fund_review.py install-schema --confirm-database mfdataindia

# Separately approved generator operation; explicit reviewed cohort and policy hash.
uv run python scripts/verified_fund_review.py generate \
  --confirm-database mfdataindia --codes 100033 \
  --expected-policy-hash <hash-from-policy-command>
```

There is no generation scheduler, full-DB sweep, provider download, policy switch,
new cloud identity or automatic dataset-version advancement. No new override or
correction can be executed through these tools.

## Verification and rollout gates

Unit tests use independent arithmetic/rational references. Isolated PostgreSQL 18
tests cover additive migration, all existing table-value preservation, restricted
principals, immutable history, stale inputs, atomic failure, replay, concurrency
and actual HTTP endpoints across sessions. These fixtures are synthetic; they
are not an operational backup/restore certificate or provider-data acceptance.

Before merging, reconcile auto-deployment behavior: main merges can trigger DEV
deployment even for dormant feature code. Merge, identity provisioning, schema
installation, trust configuration, finite generation and consumer certification
require their separately approved rollout scopes. Before live installation, test
against an operational-sized restored clone and fingerprint existing values;
stop on any unexpected change. Verify actual Cloud SQL privileges and private
ingress/IAM, then certify an explicit bounded cohort and the consumer delegation.

Rollback removes routing/access to the new feature and stops its generation;
retain the added policies, snapshots, results and history. Do not drop the schema,
delete old results or rewrite original data to roll back feature exposure.

## Preservation-first DEV rollout

`rollout-verified-review-dev.yml` is manual only, uses the existing `mf-data-dev`
environment and shared DEV mutation concurrency group, and introduces no repository
gate variables. `prepare-image` builds/publishes a provenance-labelled immutable
image only. `execute` needs a separately approved exact manifest and canonical
manifest hash; merge, CI success and image preparation do not approve execution.
The current runner is a first-install rollout, not a credential-rotation or generic
infrastructure reconciliation tool.

### Before execution approval

Run the read-only metadata prerequisite inventory first. It never fetches secret
payloads, connects to the database, provisions identities or grants IAM:

```sh
uv run python scripts/preflight_verified_review_dev.py \
  --git-sha <reviewed-40-character-SHA> \
  --source-version <numeric-version> --trust-version <numeric-version> \
  --signer-version <numeric-version>
```

Exit 2 means unresolved metadata prerequisites; lookup failures remain unknown,
not confirmed absence. Even exit 0 is `PREREQUISITES_OBSERVED_NOT_CERTIFIED`, not
permission or certification: key correspondence, effective execution permissions,
database principals and the entire backup/API acceptance chain remain unverified.
The existing runner needs explicit consumer execution delegation. Do not add it
automatically: prefer a separately reviewed consumer-owned execution arrangement
over giving the provider deployer control of the Insights runtime. Existing
optional credentials/identities/services require reconciliation before first install.

- Reconcile live resources and choose up to ten actual codes. At minimum include
  eligible Growth and unsupported-option controls. Pin additional required coverage
  for explicit `signals: null`, unavailable metrics and quality signals as applicable.
  Missing required coverage fails before any live canary event; do not fix source
  records to manufacture a passing cohort.
- Choose a canary with no previous human decisions. Approve exactly one `in_review`
  event and one proposal repeating its existing scheme name. Both are labelled DEV
  certification, retained permanently, and never assert that a real fund is reviewed
  or that an override should execute.
- Insights owns an existing Ed25519 signing secret and the corresponding trust
  document. The private signer is mounted only into a certification job running as
  its approved Insights workload, never fetched by the runner or mounted into the
  provider API. This certifies delegated provider behavior, not Insights login/RBAC.
- Approve the new API/generator identities, their restricted secret access and the
  three database principals. The governed execution account must already have the
  required narrow provisioning, Cloud SQL connectivity, source-admin-secret access,
  archive-write and Cloud Run job/deployment permissions. The runner never grants
  itself Owner/Editor, changes WIF trust or fixes shared privilege exposure.
  Running the certification job as the consumer workload also requires authorized
  `actAs` access to that identity. The runner does not grant itself this access:
  if absent, obtain a separately reviewed consumer-owned execution arrangement;
  do not casually give the provider deployer control of the Insights runtime.
- Confirm capacity and retention for the fresh private backup in
  `gs://tlws_mf_data_source/verified-review-rollouts/<rollout_id>/snapshot.dump`.
  No backup bytes are uploaded to GitHub. The unique object is create-only; existing
  objects, principals or credential secrets require explicit operator reconciliation.

### Manifest and approval

#### Existing-human-operator execution arrangement

`scripts/operator_verified_review_dev.py` supports an explicitly approved
existing project operator without expanding the provider deployer's access to
Insights. This is a separate local executor, not an impersonation or WIF bypass:
every cloud command pins `--account`, verifies that the exact human credential
exists and has an existing unconditional project Owner binding, and makes no
Owner/Editor/consumer-actAs grant. Google Cloud still authorizes each actual
operation. The project-role check is not a consumer RBAC certificate.

The operator approval JSON has exactly `format`, `project`, `account`, `git_sha`,
`operation`, `binding_hash`, `database_execution` and `backup_image`; its canonical SHA-256 must match the explicit
approved operator hash. `format` is `mfdata-review-operator-approval-v2`, project
is DEV only, `database_execution` is `private-cloud-run`, and operation is
`bootstrap-consumer-trust` or `execute-rollout`.
`backup_image` is explicitly null for bootstrap and an immutable prepared
`mfdataindia-review-tools@sha256:...` image for execution. Retained v1 approvals
and bootstrap receipts are historical evidence, not fresh execution approvals.

The regional backup worker runs as the existing restore workload beside Cloud
SQL, with its existing source-admin-secret access and an exact-rollout-prefix
temporary create-only archive grant. It exports a fresh PG18 dump under the same
MVCC snapshot as the v1 preservation manifest, then archives the dump and baseline
with generation-match-zero preconditions. It has no newly granted update/delete
or general archive-read privilege. Original source data is read-only throughout.
The controller requires actual execution completion, image/SHA/runtime/network/
secret-version pins and one structured certificate, downloads immutable object
generations and verifies size/SHA-256 before restored-clone rehearsal. The new
create-only binding is removed and checked; existing historical snapshot read
access remains untouched. No statement/lock/transaction limit is extended.

Image provenance is verified via bounded, hash-checked OCI manifest/config reads
from the exact Artifact Registry, including Linux amd64 selection and the source
SHA label. Credentials never follow redirects, and no image layers are downloaded
locally. This avoids consuming the shared container VM's remaining disk space;
do not prune images/volumes or interrupt the existing database to make space.
Bind bootstrap to the canonical hash of `trust_declaration()`; bind execution to
the exact approved rollout manifest hash. Duplicate fields, service-account
operators, wrong operations/pins and mismatched checkout are rejected before
cloud calls. Execution requires a clean checkout at the exact **live merged main**
SHA, not a locally edited branch or fabricated `origin/main` reference.

Bootstrap creates a fresh consumer-owned Ed25519 signing secret and public trust
secret, granting private-key reading only to the existing Insights runtime at
the secret level. Public trust evidence is retained before creation; the private
key is never written to local files, printed or fetched from Secret Manager by
the provider runner. Existing credentials or partial prior bootstrap stop; no
rotation or deletion is attempted. The key's issuer/audience/caller/scopes are
fixed by the reviewed declaration. This establishes delegation infrastructure,
not an implemented Insights actor login/admin-entitlement flow.

Run only after operator approval, with private manifest/evidence files outside
the checkout and a separately verified isolated PostgreSQL 18 clone server:

```sh
uv run python scripts/operator_verified_review_dev.py bootstrap-consumer-trust \
  --operator-approval <approved-bootstrap-json> \
  --approved-operator-hash <canonical-operator-approval-hash> \
  --work-directory <new-absolute-private-receipt-directory>

uv run python scripts/operator_verified_review_dev.py execute-rollout \
  --operator-approval <approved-execution-json> \
  --approved-operator-hash <canonical-operator-approval-hash> \
  --manifest <approved-rollout-json> \
  --approved-manifest-hash <canonical-manifest-hash> \
  --work-directory <new-absolute-private-backup-and-evidence-directory>
```

Set `MFDATA_REVIEW_CLONE_ADMIN_DSN` through the established environment mechanism,
never argv or output. The local executor retains backup bytes and all evidence on
success/failure as well as the create-only GCS backup. It uses the same source
locks, restricted principals, job checks, six-route certification and independent
non-destructive recovery as the reviewed runner. Source and consumer runtime
configuration remain unchanged; only the new feature's resources/privileges are
provisioned. Do not overlap this local operation with a manual CI rollout or
another DEV infrastructure deployment; reconcile their authoritative status
before execution. Scheduled refresh/audit remains enabled and is guarded by the
existing source-operation locks and input revalidation.

For this local arrangement, the atomic DEV schema/principal installation runs
in a private, zero-retry job beside Cloud SQL as the existing
`tlws-mf-data-migrate-dev` workload. The source-admin credential version and all
three newly created credential versions are immutable job pins. The job verifies
the actual database identity, exact source/role DSN binding, migration hash and
the same preservation/privilege checks before commit. Its 180-second transaction
bound is unchanged; original source rows are preserved, with only the pinned
optional migration receipt appended to the source ledger. The controller verifies
actual execution completion/image/SHA/runtime/network/secret versions and retains
the structured installation certificate before checking direct runtime logins.
Temporary migration-workload read access is removed from the three NEW credential
secrets on success or failure; all removals are attempted independently. Unconfirmed
removal fails closeout. Source-admin-secret access already belongs to the existing
migration workload and is not expanded. No private signing key is mounted there.

The operational preservation capture uses read-only binary `COPY TO` for the
ordered JSON-text column, decoded to the same UTF-8 length-prefixed row hash as
v1's named cursor. Catalog reads are batched without changing per-object ordering
or the manifest shape. A frozen v1 reference proves complete-manifest equality
on isolated PostgreSQL, including Unicode/control text, duplicates, keyed and
unkeyed tables, views, materialized views, ownership, sequences and ACLs.
This removes thousands of off-region `FETCH 1000` round trips, not integrity
checks. Source lock/statement/transaction bounds remain unchanged; measure actual
DEV capture/rehearsal before installation and stop if they cannot meet the bounds.
Source table/column privilege checks use one query over the identical complete
relation population instead of one network round trip per relation.

The JSON shape below is a template, not executable configuration. Replace every
placeholder from reviewed image preparation, the supported policy command,
SHA-256 of the checked-in contract/migration, immutable secret versions and the
actual selected cohort. Do not include DSNs, passwords, assertions or private keys.

```json
{
  "format": "mfdata-review-dev-rollout-v1",
  "rollout_id": "dev-review-20261014",
  "project": "trillionlane-dev",
  "region": "asia-south1",
  "source_connection": "trillionlane-dev:asia-south1:tlws-mf-data-dev",
  "database": "mfdataindia",
  "git_sha": "<reviewed-40-character-SHA>",
  "image": "asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia@sha256:<approved-digest>",
  "policy_hash": "<supported-policy-hash>",
  "contract_hash": "<contract-file-hash>",
  "migration_hash": "<optional-migration-file-hash>",
  "trust_secret": "tlws-mf-review-trust-dev",
  "trust_version": 1,
  "trust_hash": "<canonical-trust-document-hash>",
  "signer_secret": "trillion-insights-mf-review-signer-dev",
  "signer_version": 1,
  "source_secret_version": 1,
  "cohort": [100033, 100034],
  "actor": "rollout-certification:dev-review-20261014",
  "canary_code": 100033,
  "allow_canary_events": true,
  "required_coverage": ["growth_eligible", "unsupported_option", "explicit_null_signals", "unavailable_metric", "quality_signalled"]
}
```

The example codes/secret versions are NOT an approved or validated cohort.
`hash` and `validate` perform zero cloud/database calls:

```sh
uv run python scripts/rollout_verified_review_dev.py hash \
  --manifest rollout-manifest.json --git-sha <reviewed-SHA>
uv run python scripts/rollout_verified_review_dev.py validate \
  --manifest rollout-manifest.json --git-sha <reviewed-SHA> \
  --approved-hash <independently-approved-canonical-hash>
```

### Ordered execution and preservation gates

1. Validate approval, checkout, policy, contract, migration, trust, target and image
   provenance. Duplicate JSON members, unknown fields, unbounded cohorts, delimiter
   injection and incorrect identities fail closed.
2. Export a fresh PostgreSQL 18 custom dump using the same exported MVCC snapshot
   as its streaming preservation baseline. Archive it privately with immutable GCS
   generation and local SHA-256 evidence. Never use the historical pinned restore
   command for this rollout.
3. Restore into the runner's fresh PostgreSQL 18 server. The control database must
   be `mfdata_review_clone_control`, with no unrelated databases or roles. Restore
   owners/default ACLs; never overwrite an existing database. Compare original row
   counts/content hashes, structures, functions, triggers, effective unrelated ACLs
   and sequences. Sequence movement during backup also invalidates the rehearsal;
   it must not be silently excused as a successful preservation proof.
4. On the restored clone, install/reconcile, verify dedicated logins, publish/replay
   the cohort and exercise all six actual API routes using an ephemeral clone-only
   signer. Recheck preservation. This is `RESTORED_CLONE_HTTP`, not Cloud IAM proof.
5. Create only approved feature identities/secrets. On DEV, acquire the NAV operation
   lock and SHARE locks on existing source tables in a bounded transaction. Install
   the one optional migration, create restricted principals, reconcile and inspect
   effective permissions before commit. Revoke unfamiliar inherited default grants
   on the NEW review objects only
   and verifies the whole artifact ACL allowlist. Existing source grants and global
   default privileges remain intact; subsequent unapproved artifact grants prevent
   runtime startup rather than permitting direct ledger insertion.
   Compare preservation before commit. Any failed preservation/privilege gate
   rolls back roles, schema and migration receipt together. Lock acquisition is
   bounded to 5 seconds and the transaction to 180 seconds. Full-table fingerprint
   cost must be measured on the clone; do not extend these bounds silently.
6. Verify direct Cloud SQL logins. Stage the separate private review service, pin
   image/SHA/runtime/DSN/trust/network and verify the candidate's internal tag URL.
   Existing read-v2, association services and schedules are not modified.
   First deployment creates a private, trust-unconfigured dormant revision first
   (503/no startup DB calls), then stages the configured candidate at zero traffic.
   That dormant revision is the known first-rollout traffic recovery target.
7. Execute a zero-retry generator job, then a zero-retry private certification job.
   Require Cloud Run completion and one structured certificate bound to the actual
   execution image, identity and environment. JSON structured logs are supported;
   text JSON is a fallback. Missing/late evidence fails closed, never implies PASS.
   Execution network annotations are read from its top-level immutable metadata,
   not a fabricated TaskTemplate metadata field. See the official
   [execution shape](https://docs.cloud.google.com/run/docs/reference/rest/v1/ExecutionSpec)
   and [ObjectMeta annotations](https://docs.cloud.google.com/run/docs/reference/rest/v1/ObjectMeta).
8. Recheck the candidate immediately before promotion. Certify the service URL
   again after promotion, verify public denial and remove the candidate tag.

Normal ingestion is not paused or rescheduled. Source locks are confined to the
bounded installation transaction. Generation revalidates captured inputs before
publication; certification must match the generation result IDs. Intervening input
changes invalidate acceptance; old results/events remain available and are not
rewritten. Do not blindly rerun a failed first-install workflow.

### Failure recovery and closeout

Traffic restoration, invoker-access revocation and candidate-tag removal are
independent attempts. Candidate lookup or a failed traffic command must not bypass
access revocation. Unknown cleanup success produces
`RECOVERY_UNCONFIRMED_REQUIRES_OPERATOR`, not a rollback claim. Failed candidates,
new secrets and added records are retained for investigation; no schema/database
drop, data deletion, password rotation or backup restoration over DEV is performed.

Keep the backup, restored-clone evidence, direct-login/installation receipt,
generation execution, candidate certification and final certification together.
GitHub stores certificates for 30 days; archive required retention separately before
expiry. Closeout is `PROVIDER_DEV_CERTIFIED` only after the entire chain succeeds.
It explicitly leaves Insights RBAC acceptance, admin UI, whole-DB generation,
monthly/rolling methods and source correction activation unclaimed.

### MF-E01 DEV acceptance — 2026-10-14

The first bounded rollout and actual non-destructive recovery drill completed.
The [evidence index](evidence/mf-e01-dev-review-20261014d.json) records the exact
SHA, images, manifest/approval hashes, immutable backup generation, sealed-stage
hashes and successful Cloud Run execution identities. It is an index of retained
evidence, not a newly minted certificate or a whole-dataset assertion.

The runtime SHA is `54383575ac75e31a5684843ca372f50dac46afe0`, serving on
`tlws-mf-review-dev-dev-review-20261014d`. The separate service retains internal
ingress, IAM enforcement and only the Insights runtime as explicit invoker.
All three new database roles logged in directly and passed effective privilege
checks; temporary backup-creator and migration-secret reader grants were removed
and their removal verified. Original source fingerprints were unchanged, apart
from the explicitly permitted append of migration 023's own receipt.

The six-route certificate covers only `[101762,101763,139889,154701]`. Generation
and candidate, service-origin and recovered-service certificates agreed on exact
result IDs. It does not unlock IDCW, clear duplicate signals, manufacture missing
history or implement Insights RBAC/UI. Source counts are preservation evidence,
not analytics-coverage counts. Originals and historical derived outputs remain
intact; the new results and two certification-only canary events are sidecars.

Recovery was actually exercised: dormant prior traffic, zero explicit invokers,
external HTTP 404, then exact-revision restoration and another successful private
six-route certificate. No restore over DEV, credential rotation or data deletion
was used. The original read-v2 API, association writer and ingestion/audit
schedules were not changed. Later dataset/source/context changes can invalidate
these current-input results; do not present this dated certificate as certification
of those later inputs. MF-E02 supplies the explicit historical-publication boundary.

The private backup and raw preservation certificates remain in retained operator
storage and the named GCS generation; neither backup bytes, passwords nor signing
material enter Git/GitHub artifacts. The record contains no private keys and
does not grant the provider deployer control of Insights. Do not rerun the
first-install command against the now-existing principals/secrets/schema.

### MF-E02 publication extension — implementation, not live acceptance

`create_review_app` and its frozen six-route OpenAPI remain unchanged by default.
The explicitly selected `create_research_app` profile adds
`GET /api/review/v1/funds/{code}/publication`; its separate contract is
`contracts/mfdataindia-research-publication-v1.json`. It uses the same internal
ingress, dedicated runtime, signed backend assertions and no-store responses.
No new environment feature flag or public service is introduced.

The new response separately reports current context/input hash/dataset/source
revisions, dated certificate/result references, original metric-policy hash and
captured context. `current_artifact_certified` means API artifact conformance,
not quality clearance: callers must inspect eligibility, nulls and reasons.
Historical windows are never eligible for current comparisons. A later source
correction, identity change, disclosed quality signal, missing assessment or
unsupported calculation policy/engine prevents numeric reuse. Correct historical
values remain dated; no newer endpoint is synthesized from older values.

The opt-in SQL 024 creates one append-only certification table and a seal trigger.
It revokes owner-default grants on the new objects, grants the three existing
runtimes SELECT only, and changes no source/prior table or source migration ledger.
The installation operation stores a fixed policy binding the schema SHA, supported
metric policy/version/code hash, selection, dependencies and unavailability rules.
Existing source and prior-review fingerprints must match within a 180-second,
advisory-locked transaction. Unknown prior-review tables fail preservation closed.

`publication_operations.verified_record` reads actual immutable execution metadata
and structured logs; it does not run a job, fetch a private key or accept a locally
constructed PASS as cloud evidence. The operator checks exact existing human
authority and merged code before operations, prepares a fresh manifest and both
images, and runs the reviewed backup/rehearsal helpers. Do not reuse MF-E01's old
backup or first-install runner for this extension: the source now contains existing
review artifacts/principals. The regional backup includes their retained-row and
event-sequence fingerprints in the exported snapshot, and the fresh clone retains
them before/after the extension.

`scripts/operate_research_publication_dev.py` is a private migration-owner worker
for `install` or `register`, using only the existing source-admin secret. Registration
replays an immutable dated certificate and never updates a prior row; selection
uses actual completion time, so importing an old certificate cannot demote newer
evidence. The local operator retains source execution metadata and certificate
binding before submitting the record. `scripts/certify_research_publication_dev.py`
runs as the consumer with the existing reader/signer versions, verifies unauthenticated
assertion denial, exact raw responses against coherent DB reads and no-store,
and appends no events or source records.

For both candidate and final publication probes, set `REVIEW_AUDIENCE` to the
untagged service URL. `REVIEW_ORIGIN` is the tagged candidate URL before promotion
and the service URL afterward. The certifier rejects a tagged/missing audience
or a target belonging to another service, mints IAM tokens only for the default
audience and records both values in its evidence. This follows the [Cloud Run
service-to-service authentication contract](https://docs.cloud.google.com/run/docs/authenticating/service-to-service).

Stage the seven-route profile using the fixed `RESEARCH_FACTORY`; revision checks
also verify the actual uvicorn command/arguments. Require existing-reader policy
readiness before promotion. Retain previous traffic and revoke new exposure on
unconfirmed acceptance. Reuse the independent recovery actions; do not drop
schemas/artifacts/secrets or restore over DEV. Original six-route certification
still uses the frozen base contract. Choose an explicitly approved, unused
certification-only canary for a new rollout; never overwrite the two existing
MF-E01 canary events or any human review.

**Required compatibility ordering:** the old MF-E01 image does not recognize
SELECT grants on the new certification table in its startup privilege allowlist.
After backup/rehearsal but BEFORE installing SQL 024, stage the new image with
the unchanged six-route `REVIEW_FACTORY`, certify it and explicitly promote it.
Capture this compatible revision as the rollback target. Only then install the
extension and stage `RESEARCH_FACTORY`. Its deterministic revision suffix adds
`-research`, so the same manifest/actor/idempotent canary can serve both stages
without reusing an immutable revision name. Remove the first candidate tag before
the second stage. Never restore the pre-extension MF-E01 image after new-table
grants exist; recover to the certified compatible six-route revision instead.

Completion additionally requires observing a real normal refresh/audit cycle in
DEV and recertifying current-versus-historical behavior. No scheduler rescheduling,
manual source rewrites, legacy recomputation or quality-signal clearing is part of
that observation. Neither the implementation/tests nor a staged private revision
are MF-E02 acceptance evidence until the complete live chain succeeds.

### MF-E02 initial runtime checkpoint — normal cycle was still pending

The [dated runtime evidence index](evidence/mf-e02-dev-pub-20261014b-runtime.json)
records actual backup, clone, installation, compatible baseline, private profile
and recovery proof for SHA `8bb1f680dae5605563e7a0cc2e97f3ef254b2007`. It is not a
new certificate and does not certify future dataset versions or whole-DB coverage.
Both the base six routes and additive publication read passed on the zero-traffic
candidate and service origin, and the recovered service passed the publication
read after access propagated. Three dated certificates are stored; no original
source/prior artifact was replaced and no source migration receipt was appended.

Retain failures too: one completed baseline execution's evidence was unavailable
to its immediate observation but subsequently passed exact immutable execution
verification. Recovery restored prior traffic/closed access; reconciliation checked
current input hashes before restoring the certified compatible revision, without
reexecuting the original job. The recovery drill's first HTTP probe then received
403 / `run.routes.invoke` following re-grant; after access reconciliation and
propagation, the new read-only probe passed. IAM policy reads alone are not HTTP
readiness, and a completed job alone is not a certificate. Do not hide either
failure or relabel its outcome PASS.

The normal scheduler remains `0 */6 * * *` for refresh and `10 */6 * * *` for audit,
both `Asia/Kolkata`; the next observed UTC times are 06:30 and 06:40 on 2026-10-14.
The retained pre-cycle reference is dataset v12 with NAV as of 2026-10-13. Record
actual immutable refresh/audit executions, before/after publication responses and
input/result/certificate references. Old v12 evidence must not bless v13 inputs;
unchanged historical windows may remain explicitly dated/reusable, never current
comparison eligible. Preserve signals/nulls and retain original result artifacts.
Do not dispatch source writes or change schedules merely to speed up acceptance.

At that checkpoint the remaining gates were real normal-cycle observation and any resulting bounded
generation/certification/registration needed to establish current publication.
Those gates passed in the closeout below; MF-E03–MF-E15 are not started. A
documentation-only checkpoint merge does not redeploy or recertify a new SHA.

### MF-E02 DEV acceptance — normal cycle and fresh current publication

The [closeout evidence index](evidence/mf-e02-dev-pub-cycle-20261014.json) links the
initial archive/clone/deployment/recovery proof and actual 2026-10-14 normal cycle.
Refresh `tlws-mf-data-nav-refresh-dev-94qxz` completed at 06:31:03 UTC; audit
`tlws-mf-data-nav-audit-dev-wtnhl` completed at 06:40:49 UTC. Dataset v12 became
v13. A database observation at 06:36:12 UTC and an independent private HTTP
execution observed the pre-audit state; another pair verified post-audit behavior.
Prior review records stayed intact. No manual refresh or audit was dispatched.

Retained v12 inputs/results did not certify v13. AMFI 101762's unchanged window
remained `historical_certified` but not current-comparison eligible. The IDCW,
duplicate-signalled and unassessed controls became `historical_invalidated`, with
metrics withheld and original artifacts retained for audit. This is artifact
publication policy, not an assertion that all flagged NAVs are erroneous.

Manifest `dev-pub-cycle-20261014` then generated four new v13 artifacts under the
same immutable policy/runtime image. Its fresh canary is AMFI 154701, previously
without review history; two `in_review`/non-executable no-op diagnostic events do
not approve a fund or change original fields. Actual six-route conformance,
immutable execution/source/identity verification, certificate registration and
current seven-route HTTP reads passed. The new certificate is
`d16536ed50a97e20c157f88b3cb84db57609bbd495a351a357503a98c8d9c8e4`.
Exactly 101762 is current comparison eligible. 101763, 139889 and 154701 remain
ineligible; 154701 remains `not_assessed` with an explicit null `signals` field.

The append phase preserved the complete `mf` manifest (4,236,069 NAV rows, 14,390
funds and all other source objects/values/ACLs) and every prior review row. It
added only four snapshots, four results, two diagnostic events and one certificate;
no source rows, schema, IAM or traffic changed. A local wrapper signature error in
the final read-only check was retained. A separate corrected read-only check passed
without repeating any mutating execution. Cloud job completion and actual provider
certification are recorded separately from that local wrapper outcome.

MF-E02 is accepted for this bounded provider DEV scope. Runtime/source SHA remains
`8bb1f680dae5605563e7a0cc2e97f3ef254b2007`; the serving and compatible revisions,
source-v2 service, association writer and six-hour schedules are unchanged. This
does not certify all funds, Insights/TDW application authorization, new UI or
production. Start MF-E03 from the merged closeout; retain all prior artifacts.

### MF-E03 canonical calendar returns — implementation and bounded DEV acceptance

The opt-in `create_calendar_app` profile adds one route to the seven-route private
profile: `GET /api/research/v1/funds/{code}/calendar-returns`. The existing review
and publication factories/contracts and original read-v2 service remain unchanged.
The shared policy registry stores `growth-calendar-nav-returns` v1. Calendar years,
YTD and completed months use `(end NAV / start NAV - 1) * 100`, not annualized CAGR.
Boundaries are Gregorian calendar ends; the price is the last actual observation
on or before each target, with at most seven calendar days' disclosed lag. No
look-ahead, invented NAV, source forward-fill or underlying source edits occur.
Analysis date is the last captured fund NAV date, never the operator's wall clock.
Unfinished months/years are omitted; absent/over-budget boundaries have null
returns and reasons. Endpoint arithmetic does not certify daily continuity.

Optional migration 025 creates only `mf_analytics.calendar_results` and
`calendar_certifications`, their immutable/sealing triggers and restricted grants.
It reuses exact retained NAV snapshots and the existing policy registry; core
results and legacy monthly/yearly calculations are not repurposed or replaced.
Generator can append results, but only the migration owner may register verified
certificates. Default ingestion grants are revoked on the NEW objects only.
Unknown sidecar relations, drifted ownership/ACLs or disabled sealing/immutability
triggers fail closed. Backups include any existing calendar rows from the same
exported MVCC snapshot, and new installation compares original source/review data.

Normal reads select actual registered conformance evidence by immutable execution
completion time. Uncertified artifacts never fall back to legacy calculations.
Current eligibility is independent of certification: IDCW, quality-signalled,
unassessed or no-covered-period cases remain withheld. Changed inputs never become
current under an old certificate; supported unchanged historical windows may
remain explicitly dated and never comparison eligible. An exact `result_id` query
is a retained **audit-only** read and requires `review:admin`, not just read scope.
It may expose an ineligible/staged artifact for conformance, not an eligibility
override or a fund correctness decision. All responses are private/no-store.

DEV rollout sequence under the standing fifteen-sprint approval:

1. Freeze exact merged SHA, immutable application/PG18 tooling digests, policy,
   original and additive contracts, trust/key/credential versions and <=10 funds.
2. Take a fresh create-only private archive; restore onto a new empty task-owned
   PostgreSQL 18 clone. Rehearse 025, effective restricted logins, immutable replay
   and preservation of every original source/review/calendar record. Never restore
   over DEV or delete old clones/backups.
3. Use a migration-owner job to install the opt-in extension, then restricted
   generator jobs to append a bounded cohort. No source migration receipt is
   appended; no core/legacy result is overwritten.
4. Stage the calendar factory at zero traffic. Verify immutable image/SHA/runtime
   identity, network, numeric secret versions and private access. Run read-only
   calendar conformance through the tag using the service's default IAM audience;
   preserve/assert the original six and seventh routes as well.
5. Promote the verified revision, remove tags, run a fresh service-origin calendar
   certificate, then independently verify its immutable Cloud execution with
   `analytics.operations.verified_record`. Register that exact record in an owner
   job. Certify current private reads and original endpoint compatibility.
6. Verify the exact serving configuration, public/assertion/audit-scope denial,
   source/prior-row preservation and non-destructive recovery to the retained
   seven-route profile. Record actual evidence before accepting MF-E03.

No UI, source correction, public MFData API, consumer RBAC or whole-dataset
analytics acceptance is included in this implementation. Source acquisition and
IDCW adjustment remain later gated work; calculations use already-held NAV data.

#### MF-E03 DEV closeout — 2026-10-14

[PR #43](https://github.com/trillionlane/tlws-mfindia/pull/43) merged as
`a4d253a2a26baaa6e88bbe472a71a3335279000c`. Exact-head CI passed 772 tests,
with one skip, and all three image builds. The [retained live evidence index](evidence/mf-e03-dev-cal-20261014a.json)
records actual archive/clone, database and private HTTP acceptance, not another
locally manufactured certificate. Runtime SHA is the implementation merge SHA;
this documentation-only closeout does not redeploy or relabel it.

The create-only archive retained GCS generation `1791967896140980` and SHA-256
`774419bd497ab0279a68ac9bdb3bfb5605cee78bc45443209444f843eac6b4e8`.
An operational PostgreSQL 18 restored clone rehearsed the additive installation,
actual restricted logins, replay, API authorization and preservation. The private
dump, credentials, signer and full raw certificates stay outside Git. No restore
over DEV, deletion, IAM expansion or original source correction was performed.

Migration-owner execution `mf-cal-install-dev-cal-20261014a-7rl54` installed
the optional extension. Restricted generation appended exactly four calendar
results for `[101762,101763,139889,154701]`. Core compatibility generation replayed
the already-held core results; it did not append or replace core snapshots/results.
Candidate and fixed-origin original-six, publication and calendar certificates
passed independently. Owner registration accepted only the verified immutable
fixed-origin execution, producing certificate
`a522817116e4697e2f56c4158c8f8e92bec21b8693ddf57dcde6485a9ff75124`.
Post-registration reads were certified again.

The eight-route profile serves 100% untagged traffic at
`tlws-mf-review-dev-dev-cal-20261014a-calendar`, with internal ingress, the dedicated
restricted review runtime and the existing sole explicit Insights invoker. The
mixed original review profile uses the restricted `mfdata_verified_admin` login;
independent read-only certifiers use `mfdata_verified_reader`. AMFI 101762 has
64 available periods and 11 reasoned unavailable periods in the retained audit
artifact; its ordinary calendar projection is eligible. The other three controls
remain ineligible and ordinary reads withhold their numeric calendar objects.
AMFI 154701 retains explicit `signals: null` and `not_assessed`. Certification
does not clear a duplicate signal, invent missing history or unlock IDCW.

Recovery actually promoted the retained accepted seven-route revision and ran
its private publication check, then restored the exact eight-route revision and
ran another calendar certificate. Recovery changed traffic only: source, schema,
artifacts, credentials and IAM were retained. External unauthenticated review and
calendar probes returned 404 after restoration; there are no retained tags.

The final read-only comparison against the fresh archive preserved every original
source value/object/sequence/owner/ACL, including 4,236,069 NAV rows and 14,390 funds,
and every prior review row. Allowed additions were one calendar policy, four
calendar results, one calendar certificate and two bounded diagnostic canary
events for AMFI 101763. These `in_review`/no-op proposal events are conformance
fixtures, not human correctness approval or executable data corrections.

MF-E03 is accepted only for this declared provider DEV cohort at dataset v13.
Future source/context changes must remain dated or require recertification under
the stored policy. MF-E04 rolling analytics is next; MF-E04–MF-E15 are not completed
by this record. No Insights/TDW application authorization, UI, licensed-source
acquisition, whole-universe analytics coverage or production claim is made.

### MF-E04 finite scope — implementation and bounded DEV acceptance

Add immutable anniversary rolling-return artifacts for 1-, 3- and 5-year terms.
The schedule is completed Gregorian month ends, independent of chart settings;
it is explicitly not a daily rolling series. Start targets subtract calendar years
and clamp leap-day anniversaries. Both prices must be actual observations on or
before their target, with at most seven disclosed calendar days of lag. Annualize
the endpoint ratio using actual observed elapsed days and a stored 365.2425-day
year; do not substitute trading-day counts. Disclose interior gaps without
claiming daily continuity or inventing observations.

The stored policy defines Decimal precision/rounding, bounds, source/context/code
lineage and replay. Summary min/max/median/arithmetic mean/positive-window share
uses unrounded window values, requires at least two covered windows and complete
scheduled endpoint coverage. Incomplete summaries are null with a reason.
The positive-window share describes this fund's observed monthly windows; it is
not a probability forecast or whole-market statement. Unsupported IDCW, absent
history, missing endpoints and source quality restrictions remain explicit.

Use a separate append-only `mf_rolling` namespace to retain the accepted calendar
schema and its ACL boundary unchanged. Restricted generation may append sealed
results; only the migration owner may register verified fixed-origin conformance
evidence. Add a private opt-in read profile without changing existing frozen
six/seven/eight-route or source-v2 contracts. Bounded paging must bind the retained
result/term and reject inconsistent cursors; ordinary readers cannot bypass
eligibility through retained audit access. No new per-gate environment flags.
Normal response comparison eligibility applies to the requested term: another
term's complete summary cannot make an unavailable five-year request eligible.

Acceptance remains fresh archive/restored-clone preservation and replay, owner
installation, bounded generation, original/additive private candidate/final
certification, exact immutable-execution registration, recovery/restoration,
public/reader/admin denial and full-source/prior-sidecar comparison. Real restored
DEV data supplies 48 one-year and 24 three-year covered windows for AMFI 101762,
but no five-year completed-month window yet. Five-year arithmetic has independent
fixtures; do not relabel a fixture as real DEV coverage or invent a longer history.
No UI, source correction, source acquisition, whole-dataset or consumer RBAC claim
is included. The Sprint 3 profile was the serving and recovery target throughout
staging; the separate Sprint 4 gates passed as recorded below.

#### MF-E04 DEV closeout — 2026-10-14

[PR #45](https://github.com/trillionlane/tlws-mfindia/pull/45) supplied the feature;
[PR #46](https://github.com/trillionlane/tlws-mfindia/pull/46) fixed the rolling
CLI's copied calendar operation/record keys and added seven actual-entrypoint
regressions. Exact-head CI passed 833 tests, one documented skip and all image
checks. [The evidence index](evidence/mf-e04-dev-roll-20261014b.json) reconciles 25
retained sealed stage documents; it is not a newly minted certificate. Runtime
SHA is `dad7191576776dc4d36291557bfc6e5da03ab753`, not this documentation merge.

The initial installation execution failed with `KeyError` before database
connection. A fresh read confirmed no rolling schema and unchanged Sprint 3
traffic. Its archive, clone and failure proof remain retained. The corrected
attempt `dev-roll-20261014b` used fresh immutable application/tooling digests and
archive generation `1791975239462882`, SHA-256
`88d8bc6536730098069f1f47f75357272eeb80d2bc7a546b262a7200ff215d45`.
Clone B's restore was killed by the local 2 GiB VM's OOM killer; the failed clone
was retained. Only positively identified idle task clones were stopped, never
deleted. A new empty memory-bounded clone C passed operational restore, original
source/prior-layer preservation, restricted direct logins, replay and private
HTTP authorization. The user's existing database container was not modified.

Actual migration execution `mf-roll-install-dev-roll-20261014b-bwb2l` installed
only the optional rolling namespace and policy. Restricted generation appended
four rolling results for `[101762,101763,139889,154701]`. Original core/calendar
generation replayed existing rows without replacement. Source inputs, policy and
code match the clone; runtime-specific result IDs legitimately differ because
the clone records Python 3.12.13/Decimal 4.0.0 and deployed derivation records
Python 3.12.15/Decimal 2.5.1. Final private conformance recomputes in the deployed
runtime and verifies exact stored result IDs and values.

Original six-route compatibility used the exact retained MF-E03 consumer image,
manifest, canary body and idempotency keys against the new provider. It added no
review/proposal event and did not pretend an old consumer SHA was the new provider
SHA. Candidate and fixed-origin original-six, publication, calendar and rolling
checks passed independently. Verified owner registration produced certificate
`7414be4e2b41b6331e157c71aa1dc6a71c4f6351d8fa6dd6743968ddd5bbb10f`;
post-registration ordinary reads were certified again, including complete bounded
cursor traversal. Only AMFI 101762's one-/three-year requests are eligible.
Five-year coverage remains null/reasoned and comparison-ineligible; IDCW,
duplicate-signalled and unassessed controls expose no ordinary numeric windows.
AMFI 154701 retains explicit `signals: null` and `not_assessed`.

The nine-route revision `tlws-mf-review-dev-dev-roll-20261014b-rolling` serves
100% untagged traffic, with internal ingress, the dedicated restricted review
runtime and the existing sole explicit Insights invoker. Actual recovery promoted
the retained eight-route calendar revision, ran its private calendar check,
restored the exact nine-route revision and recertified rolling reads. No schema,
record, credential or IAM deletion/rewriting was used; external review/rolling
probes returned 404 after restoration.

The final remote read-only COPY hit its unchanged statement timeout. That failure
was retained. The same reviewed regional MVCC backup/helper then completed as
`mf-review-backup-dev-roll-close-20261014-swxgb`, preserving a create-only post-rollout
archive and full source/review/calendar/rolling manifests from one snapshot.
Comparison proved all original values, objects, functions, triggers, sequences,
owners and ACLs unchanged, including 4,236,069 NAV rows and 14,390 funds. Every
prior review/calendar row and the review sequence remained identical. Additions
were exactly one policy, four rolling results and one rolling certificate; no
core snapshot/result, calendar artifact, review event or source migration receipt
was added. Timeout limits were not relaxed and no mutating job was repeated to
resolve this read-only verification failure.

MF-E04 is accepted for this declared provider DEV cohort at dataset v13. No
whole-dataset analytics, human fund correctness, consumer application RBAC, UI,
licensed-source acquisition or production acceptance is claimed. MF-E05–MF-E15
remain pending. MF-E05 must retain the source-access gate above: written reuse/
retention permission or a permitted authoritative source is required before AMC
enrichment imports; this closeout does not supply or waive that permission.
