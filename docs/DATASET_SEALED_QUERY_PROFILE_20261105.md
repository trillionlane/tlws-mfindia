# Sealed dataset query profile v1

The dataset foundation is merged at `abe305a3848e48498bbafc402daf44a417389d0d`
(PR143). Five eligible existing business projections exceed the dataset API's
unchanged ten-second data-query guard on the current DEV population:
`mf.current_fund_facts`, and `mf_live_facts.field_heads`, `selected_fields`,
`current_values`, and `resolved`. SELECT permission and schema discovery do not
establish that their pages can be consumed.

## Execution correction

`datasets.projections.inspect` retains the existing full installed-policy,
definition, owner, options and retained-prior comparisons. It returns an
immutable request-local graph alongside approved OIDs. The existing one-per-
inventory dependency scan remains authoritative; neither that graph nor SQL is
included in consumer metadata, schema fingerprints or cursors. There is no
cross-request security-verdict cache or additional connection.

Only those five already verified projections under a known AMC-book policy
select `mfdata-sealed-projection-read-v1`. `datasets.query_profile` shares the
retained prior graph, extracts repeated batch JSON properties once per batch,
and shares selected fields/current values within the same read-only query.
It uses the original sealed SELECT bodies. Finite reference/expression
substitutions must have exact byte inverses at SQL token boundaries; quoted
values are preserved, and unsupported shapes fail with the fixed
`DATASET_QUERY_PROFILE_UNSUPPORTED` reason instead of silently reverting an
approved target to the slow generic path. Earlier non-book installations and
ordinary eligible datasets keep the generic source.

Independent review rejected the first collision guard: an inner CTE with an
optional column list could capture the rewritten reference. Five adversarial
cases first failed at that source. Token-level balanced column-list scanning
now rejects bare, quoted, mixed-case and nested declarations, without treating
escaped or doubled-quote literal contents as SQL declarations.

No stored view, financial expression, source/decoder/allocation policy,
registry, seal, index, grant, role, image command, workflow or public contract
is changed. Registry v4 is not an execution-SQL version. This profile uses
the actual retained definitions, including the existing single-selected-fields
and UNION execution shapes, rather than assuming an older six-scan graph.

Page projection, all fields, scalar conversions, keyset/NULL/canonical-offset
ordering, cursor binding and lookahead, binary selectors and response budgets
remain in the existing shared reader. The RR READ ONLY transaction, ten-second
statement guard, five-second lock guard, 100-row page, 2 MiB response, 4 KiB
decoded cursor and 128 KiB binary chunk limits are unchanged. Existing domain
point-read APIs are not routed through this global paging strategy.

## Verification and evidence limits

Read-only DEV diagnostics before implementation found all five original
initial pages timing out at ten seconds. A query-local prototype completed the
five initial pages in roughly six to eight seconds without persistent changes.
Bounded same-snapshot comparisons preserve complete document fingerprints;
the two wide views require scalar point predicates for the original comparison.
An earlier two-code ANY diagnostic timed out and remains unverified evidence,
not an inequality or permission failure. These timings are observations, not
a production SLA or proof of all pages, all financial cohorts, or TI acceptance.

Provider-free tests cover target selection, generic preservation, immutable
graph handling, literal and token boundaries, finite inverses, retained-prior
requirements, unsupported shape refusal, existing descriptor/cursor/security
checks and unchanged packaged entrypoint behavior.

The new hosted-only native case uses the existing coherent installed-v4
fixture and real synthetic SBI source constructors/publication. It compiles
the pinned book/core projection through production configuration constructors;
it does not invent a live activation receipt or change the registry. Positive
book observations, equal-value corroboration, conflicting values and retained
prior rows feed comparisons of every typed field for all five views. It then
pages the actual API beyond page one, asserts real profile selection, rejects
changed seals, denies reader mutation/elevation and exercises generator
publication/replay after broad adoption. Existing nullable/duplicate cursor,
binary, authorization, unsafe dependency, creator and enrollment tests remain.

Final-head local and hosted native/image/contract/audit/credential results are
recorded in the PR. Local collection or doubles are not native PostgreSQL proof.
No local PostgreSQL or hosted-environment spoofing is permitted.

The synthetic publication receipt uses the pinned v4 registry's existing job
prefix. A provider-free regression checks all three fixture IDs against the
unchanged migration's execution-ID constraint; the earlier abbreviated IDs
failed this check. Neither the database constraint nor runtime identity rules
are relaxed to accommodate fixture data.

## Release and recovery

The target remains 127 accounted: 100 eligible consumer datasets, 26 excluded
internal controls, and one excluded raw transport relation. Enrollment, image
publication, candidate preparation, authenticated 100-item API sampling and TI
consumption remain separately observable release steps. No current dataset
registration or new TI acceptance is asserted by this source change.

Use the existing explicit owner baseline-enrollment transaction after fresh
metadata/comment reconciliation. The prepared 100-item baseline expects zero
GRANT statements; stop on drift rather than applying stale SQL. Do not run the
general deploy workflow unmodified if it would replace the dedicated-reader
connection or touch unrelated writer/migration/refresh jobs. Stage only the
reviewed core image/configuration through the existing bounded release process.

Hold a failing no-traffic candidate. Recover captured core traffic only for an
actual operational problem or explicit instruction; preserve successful reader
grants, compatible generator/review definitions and enrollment evidence. Delayed
manual testing is not a recovery trigger. Do not automatically revoke, reseal,
retry an uncertain mutation, acquire sources, rerun UTI ingestion, or alter IAM,
schedules, writer connections or financial records.
