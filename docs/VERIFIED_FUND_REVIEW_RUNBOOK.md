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
