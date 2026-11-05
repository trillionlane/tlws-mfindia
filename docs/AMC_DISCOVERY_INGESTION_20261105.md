# Shared AMC discovery and ingestion

This change composes the existing adapters and publishers. It does not certify
new issuer books, all-AMC coverage, a live deployment or successful DEV loading.
The Kotak repair is the prerequisite in PR145. The source review and live
publication evidence remain separate.

## Execution composition

The fixed job still has six independent tasks: Small-ABSL, HDFC, expanded ABSL,
SBI, Kotak and UTI. `scripts/refresh_registered_amc_gate_dev.py` selects the
explicit v7 book profile and current v3 workload allocation; default v5 remains
dormant. `--validate` snapshots the real environment and performs no provider or
database I/O. `--check-installed` is read-only. `--kotak-only` admits exactly one
task/index and the fixed Kotak source; it cannot silently invoke the full job.

Each adapter discovers current issuer-linked files, uses its original bounded
transports, and validates actual PDF dates during preparation. A callback
captures the actual publisher invocation as a typed `Prepared` value. It does
not invoke the publisher or manufacture a committed receipt. Failed preparation
cannot append an old-style publication failure receipt before a shared claim.
The existing source, identity, full-cohort, decoder, lock and publication checks
remain in the actual publisher.

Work identity binds source, the actual latest reporting date (with both required
periods retained), role-specific PDF/XML SHA-256s, derivation version and current
population/issuer-directory identity. UTI/SBI Active and Passive roles cannot be
swapped under the same key. Revised document bytes are a new candidate even for
the same reporting date. Mutable catalogue/index observations are retained as
references and a poll hash; a timestamp/nonce change does not create new
financial work. Before skipping derivation, all previously consumed input bytes
are fetched and rehashed, fresh index-to-XML links are checked, and the installed
scope is verified. ETags, filenames and a matching PDF alone are insufficient.

A short PostgreSQL transaction takes a work-key advisory lock and durably claims
the exact candidate. The same source/execution cannot claim another candidate.
A pending claim never expires into permission to retry. A distinct future
execution can retry a confirmed rollback, but cannot replay an uncertain write.
The publication intent records the real writer PID/start before the publisher
is called. Its existing receipt and the shared completion event commit in the
same business transaction. Source/period/cohort SQL guards still execute.

Books retain 30s statement/60s transaction limits; Small-ABSL retains 120s/240s;
HDFC retains 120s/180s. Lock acquisition remains 5s, the common task wall is at
most 520s, and the existing job/task/resource/retry allocation is unchanged.
There is no extra in-task retry, source HTTP bypass, new worker family or queue.

## State and failure matrix

| State | Meaning and effect |
| --- | --- |
| WAITING_FOR_PUBLICATION | Checked issuer channel has no eligible files; last valid data remains served. |
| INCOMPLETE_SOURCE_FILES | Required issuer-linked pair/period set is incomplete; no publication. |
| UNCHANGED | An atomically completed identical input/derivation is verified; skip PDF derivation and publication. |
| FETCH_FAILED | Request, source response or original retention failed; preserve last valid period. |
| PARSE_OR_VALIDATION_FAILED | Actual date/layout/identity preparation failed; no business publication. |
| PUBLICATION_ROLLED_BACK | The writer body failed and rollback returned on an intact idle connection. |
| PUBLICATION_OUTCOME_UNKNOWN | Submission/COMMIT outcome is uncertain; stop, do not replay. COMMITTED_UNVERIFIED remains a distinct strongest effect. |
| PUBLISHED | The existing guarded receipt and completion committed together. This is not consumer acceptance. |
| HELD_FOR_RECONCILIATION | Durable claim/history or control state prevents another submission. |
| NOT_DUE | Source cadence has not elapsed; this poll does not postpone the next eligible discovery. |
| ADAPTER_NOT_AVAILABLE | No supported publication path for the observed candidate; never silently substitute an older edition. |

Operation, stage, claim, strongest effect and allowlisted reason survive reporting
and diagnostic-persistence failure. Raw exceptions, SQL, DSNs, credentials and
provider bodies are not emitted. `Store.inspect` is SELECT-only in a read-only
transaction and never creates, publishes, reverses or resets an operation.
Missing completion is UNKNOWN, not evidence of no publication. Memory cannot
replace durable history. Confirmed completion dominates a later unknown event.

## Sources and retained coverage

| Source | Current behavior and boundary |
| --- | --- |
| Small-ABSL | Existing official catalogue/adapter. The explicit replay seam accepts only the reviewed 6.20.1 to 6.21.4 change in the three decoder-version labels when every fact, text/engine/policy hash, identity and other field agrees. Old snapshots remain exact; the new candidate comparison is committed with control evidence. All other differences remain conflicts. |
| Expanded ABSL | Existing latest-two-period discovery, retained issuer identity and publisher. |
| SBI | Existing complete latest-two Active/Passive sets; partial newer pairs remain incomplete. |
| Kotak | PR145's layout method, two actual periods, complete identity population and unchanged historical fallback expiry. |
| UTI | Existing ordinary English catalogue cadence; zero rows is waiting, a missing Active/Passive member is incomplete, and PDF dates must validate before publication. No provider activation of dormant UTI v4. |
| HDFC | New issuer-link discovery selects an edition dynamically. A dynamically linked known original is reported unchanged only when its actual existing publication is verified. The old two-request allocation is retained; no third identity request or pinned publication is silently added. A new edition is held for a separately reviewed dynamic publication method. The fresh official listing request returned HTTP403: this is a fetch failure, not proof that books are absent. No CAPTCHA/HTTP-denial bypass or guessed edition URL. |

`amc_loading_coverage.build` accounts for every retained census member and keeps
identity mapping, adapter support and feed state separate. The retained 16 October
census has 57 entries: five configured AMCs/six feeds and 52 without adapters;
one entry lacks an exact database identity match. Ambiguous/unmatched identities
are not automatically eligible. This dated snapshot remains visible on a failed
AMFI refresh. Directory links do not allocate acquisition or imply a working
adapter, freshness, legal active status or whole-market completeness. HDFC's
new-period publication limitation is not counted as completed dynamic ingestion.

## Installation and compatibility

An owner installs `sql/operations/amc_ingestion_gate_v1.sql` through
`ingestion_install.install` inside its existing transaction, after the explicit
v6 prerequisite is present. It selects v7 and seals the installed control
catalogue. Re-entry verifies exact definitions/ACLs/columns/constraints/indexes/
triggers and returns; drift is rejected. No historical numbered migration is
edited or replayed. Existing source rows and receipts remain immutable.

The operational schema is outside the sixteen consumer-enrolled schemas. Only
the existing generator gains SELECT/INSERT on its append-only control tables;
readers receive no access, role membership, grant option, business writes or DDL.
Existing source publishers retain their own capabilities. All six predecessor
source configurations and both previous workload allocations are preserved by
finite whole-byte inverse bindings. V7/current allocation bind actual current
execution sources; historical hashes are not relabelled as current proof.

Deploy compatible API/review/generator code before changing the installed source
profile. A historical image that cannot recognize the new configuration is not
a valid rollback after that transition. Retain the healthy compatible definitions
and last valid data on source failure. Do not delete configuration generations,
reset claims or replay a possibly committed publication.

## Validation and outstanding acceptance

Provider-free tests cover typed empty/partial discovery, role-sensitive and
revised-input keys, catalogue nonce stability, fail-isolated source progress,
strongest effects after reporting failure, explicit CLI environment/allocation
validation, complete census accounting and strict dependency-only replay.
Packaged smokes execute the real main and shared cycle with labelled synthetic
transports, in addition to the existing actual isolated PDF-worker smokes.

Hosted native cases exercise advisory claims under concurrency, actual existing
source publication and atomic completion, duplicate prevention, read-only
inspection, owner installation/idempotence, real main/adapter/SQL composition,
authenticated reads and reader denials. A separate native regression first calls
the committed old publisher to reproduce the Small-ABSL uniqueness conflict,
then proves exact retained replay through the explicit new seam. No local
PostgreSQL or live-source test is represented as this evidence.

These hosted results are PENDING until recorded at the final PR head. Required
full local/contract/audit/credential and both-image checks must also pass.

The live schedule is unchanged: one daily trigger, `0 9 * * *`, Asia/Kolkata.
A proposed `0 9,15,21 * * *` uses the same scheduler/job/body/identity, with
per-source six-hour month-opening discovery and daily revision checks. It is
not applied by this source change. Increase it only after the required native
transition/concurrency/isolation proof. A scheduler HTTP acknowledgement is not
a successful publication, and a manual invocation is not a scheduled run.

Still required: independent source review; actual Kotak DEV recovery and API
readability preserving September; accepted release/current-profile installation;
HDFC dynamic-publication completion and live issuer availability; one genuine
DEV scheduled publication and a repeat poll with zero duplicate business
artifacts. Do not manufacture a new reporting period or bump a derivation merely
to create a scheduled-publication witness. The UTI source-publication wait and
all original source/data preservation obligations remain explicit.

The first full local pass retained 5,852 passing non-native cases and exposed
three stale current-version assertions (v7 was still treated as unknown or the
known set stopped at v6). Their correction explicitly adds the current v7
profile, tests context isolation for it, and keeps v8/incorrect types rejected.
All historical PDF bytes, old policy hashes and dormant default-v5 assertions
remain exact. The focused compatibility set passes 103 cases; the corrected complete local pass has all 26 checks successful: 5,855 non-native
cases, 582 PostgreSQL cases deselected, 38 subtests, all 21 contracts, lint,
credential scan and dependency audit. The private unpublished package is not
audited by PyPI audit. Collection retains every prior case and marker in order:
6,437 unique cases, 13 focused, 6,424 complement, 582 PostgreSQL; 47 additions
(44 unit, three native), zero removed. Hosted native/image acceptance remains pending.

The first hosted gate run, 42033150256 at 593d9d9, is failed and inadmissible:
13 focused and 6,422 remainder cases passed, one new fixture failed and one old
case skipped. Both images passed; downstream PostgreSQL audit/credentials were
skipped after that failure. The fixture created an unsealed private operational
schema before broad adoption. The existing owner preservation guard correctly
rejected it as unmodelled. Further caller tracing identified a real composition
requirement: later supported owner reconciliation must still account for the
installed control schema, rather than merely changing test setup order.

Current preservation now recognizes only the sealed `mf_ingestion_ops` layer,
checks its exact inventory/catalogue seal, and exposes its complete streaming
row-hash capture independently, like the other append-only sidecars. The core
manifest also binds its catalogue/structure; operational rows are permitted to
append during ingestion, without changing original core source rows. Both
full and structure comparisons reject control-structure changes. Absence of the
layer leaves the historical core manifest shape unchanged. Unknown other
schemas and unsealed/drifted control objects remain rejected. No broad schema
exemption, ignored rows, extra reader grant or timeout change is introduced.
Metadata owners use role names, avoiding transient role-OID seal bindings.

Both connected cases reuse the same supported v4 -> layout6 -> gate7 setup.
The concurrency case now publishes the actual current method's October and
September pair. It proves owner reconciliation after control installation,
complete control-row preservation through reconciliation, core preservation
through operational appends, reader denial on controls, and rejection of an
unknown namespace or control table. Kotak's independent native case retains
the prior September/v4 replay checks. Final new-head native acceptance remains
pending; no failed result is relabelled or refetched.

The corrected complete local pass has all 26 checks successful: 5,858 non-native
cases, 582 PostgreSQL cases deselected, 38 subtests, all 21 contracts, lint,
credentials and dependency audit. After that pass, the three new database
composition/replay cases were moved into the existing early partition. This
changes order only, retains all old focused cases, and still runs the complete
collection exactly once. The partition regression failed before the change;
84 focused partition/admission/preservation checks pass afterward. New hosted
results must establish native acceptance at the corrected head.

Before source acceptance, a further replay edge was reproduced with the actual
runner and claim boundary: changing document bytes, reporting period or derivation
version produced a new work key while another claim for the same source was
unresolved. All three fault regressions failed the green 0cb8064 implementation.
That run remains genuinely green for its tested source, but is not acceptance
of this newly added condition.

Claim decisions now take a short source-local advisory lock before the retained
work lock. Before allocating a new claim, they check unresolved history across
all keys for that source. An actual atomically committed PUBLISHED event resolves
its work; confirmed rollback without an ambiguous event permits another distinct
candidate. Unresolved effects never expire or become fresh merely through a new
PDF, period, execution or release. Other sources retain independent claim locks.
No business publisher, SQL schema, lease, deletion, role or transaction bound is
changed. Known identical published input still returns unchanged without work.
The connected concurrency case now checks all three changed-key holds, independent
source progress and resumption after an actual observed writer rollback, while
preserving all business documents and generator capabilities. New own full CI is
required; no failed or earlier green result substitutes for the corrected head.
