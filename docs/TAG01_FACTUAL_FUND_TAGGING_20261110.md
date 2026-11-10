# TAG 01 factual fund tagging foundation

Base: `db07e53a509a39556214bec80cca084086c37b2a`. This is code and disposable-fixture delivery, not live fund classification or DEV activation. NSE01-A/PR149, HDFC, source acquisition, financial calculations, schedules, TI changes and green/ethical screening are outside this slice.

## Behavior and policy matrix

The current family builder replaces untyped `mf.fund_family.tags`; association v1 accepts only scheme aliases. Neither is changed. The new optional `mf_tagging` sidecar publishes factual database identity observations by canonical AMFI code, with original observation hashes, dates, policy versions, immutable historical assessments and a physical current read model. No external certification, current portfolio, freshness or consumer-acceptance claim follows from these records.

| Input or dimension | TAG01 behavior |
|---|---|
| AMC, category, structure, plan, option, ETF marker | Exact typed observation, only with a source observation date |
| Category not AMFI-authoritative | Category and derived asset class withheld |
| Name-inferred/unknown plan | No positive plan classification |
| Asset class | Three explicit AMFI category-prefix mappings; unknowns withheld |
| Risk | Not assessed: no dated authoritative risk source in this slice |
| Mandates, geography, themes, holdings, ESG/values screens | Explicit `not-assessed`; no name inference |
| Out-of-current-scope schemes | Census retained, every dimension `not-applicable` |
| Family missing, wrong UUID or multiple AMC/type/category groups | No family-level link; independent scheme facts retained |
| Identical complete input and policy | `UNCHANGED`, no new snapshot or assessment |
| Revised same-period evidence or changed membership/name/date | New immutable snapshot; previous evidence retained |
| Previously published input returns after a different head | Reject pending review; no silent head rollback |
| Error anywhere before COMMIT | The transaction rolls back; no partial current/head/history |
| COMMIT/connection failure | Propagates; no retry or automatic recovery. Reconcile durable state before another operation |

The analysis proposed 18 broad taxonomy families. TAG01 expands the initial identity/terms families into eight independently assessed factual attributes and retains twelve future-family placeholders: twenty assessment slots per stored scheme. This is deliberate finer grain, not twenty positive labels. Exact source strings determine namespaced tag IDs; lossy display slugs cannot merge distinct values. `GROWTH` here is a distribution option, never a growth investment strategy.

`currency=not_certified` and `basis=observed_database_identity` are explicit. The evidence date is the database identity row's `last_seen_in_source`, not a claim about SID effective dates. No policy threshold or universal moral/faith certification is introduced. No manual tag is placed into the existing replaceable array or alias v1.

## Trusted composition and transaction

`tagging.store.install(conn, repository_root)` requires a direct `mfdata_admin` connection and an existing caller-owned transaction. The separate `mfdata_tag_writer` role must already exist without elevated role attributes or membership; the installer does not provision it. Reviewed additive SQL is hash-bound and installed once; re-entry checks the schema marker/owner and fixed relation population. Definitions include an explicit policy version in their uniqueness key, so older vocabulary versions remain representable. Historical numbered SQL, core migrations, all 21 existing OpenAPI contracts and source-authority pins remain unchanged.

The installer grants the writer column SELECT only on four existing identity tables and derived-data privileges only in `mf_tagging`. It grants the existing reader schema USAGE and SELECT on `definitions` and `current`. Those two safe physical tables are registered through the existing `datasets.enrollment.enroll` seam. Snapshot reports, original row observations, historical assessments and the head record are not enrolled or granted to the reader. No global defaults, role membership, owner promotion, source write or new privileged helper is introduced.

`tagging.store.publish(conn)` requires an idle direct writer connection and owns one repeatable-read transaction. It takes a nonwaiting transaction-scoped publication lock, applies the existing short-operation limits (5-second statement, 5-second lock, 15-second transaction), reads the entire canonical census once, prepares deterministically, inserts bounded batches, verifies the exact current projection, historical assessments, original observations, definitions and snapshot metadata, and changes the head in the same transaction. The identical-input path performs the same complete readback before reporting `UNCHANGED`; damaged state stops without repair or replay. It uses no provider or secret transport and does not invoke another publication transaction. The census rejects more than 50,000 schemes or 64 MiB of input rather than silently publishing a truncated subset. These are new upper bounds, not a measured full-universe runtime claim.

A failed classifier does not modify ingestion/source tables. Automatic post-ingestion hooks and scheduling belong to TAG05. TAG01 never starts a background worker, modifies a live connection or enables a browser writer.

## Offline command and read model

`python -m mfdataindia.tagging --input synthetic-census.json` is an offline complete-census validator. It accepts only the exact documented `policy.ROW_KEYS` list, rejects duplicate JSON members and invalid/bounded data, ignores environment DSNs and cannot connect or publish. It returns hashes/counts with `OFFLINE_VALIDATED_NOT_PUBLISHED`; invalid input emits a stable value-free reason. A successful offline result is not an install or grant approval.

The read seam is the existing authenticated dataset API:

- `GET /api/datasets/v1`
- `GET /api/datasets/v1/{id}`
- `GET /api/datasets/v1/{id}/rows?limit=...&cursor=...`

Dataset IDs are derived by the existing `datasets.policy.dataset_id('mf_tagging', relation)` function. Current authentication, metadata/seal checks, 100-row/2-MiB limits and cursor behavior remain unchanged. There is no tag-specific HTTP mutation endpoint. TI's semantic filters and display contract belong to TAG02; its exact scheme-alias news rail is unchanged.

## Verification

Provider-free regressions exercise actual preparation and the actual offline `main()`: typed-value preservation, the legacy replaceable-array gap, full accounting, unknown evidence, no ethical inference, family collision/rename/orphans, plan/option separation, reserved dimensions, stable replay/revised inputs, duplicate/invalid/oversized inputs, exact source hash and zero database calls. Native tests reuse the existing hosted-only `ro01_db` fixture and actual migrated schema, broad-reader adoption, real narrow writer, installer, publisher and existing dataset API. No local PostgreSQL, Docker, cloud or source calls are used.

Native gates cover transaction rollback after current deletion, re-entry, two snapshots after family rebuild, immutable original evidence, paginated reads, reader mutation/DDL/elevation denial, writer source mutation denial, missing and unsafe reader grants, unavailable internal controls, concurrent publisher rejection and preservation of generator/admin effective capabilities.

CI ASGI requests prove real PostgreSQL and route behavior; existing infrastructure/TI controls supply authentication, not a fabricated in-process Google identity. They do not prove live signed-in TI acceptance. Initial stable full local verification: 5,958 non-PG tests and 38 subtests passed; 590 PG cases deselected. All 26 required local checks passed, including 21 contract exports, lint, third-party dependency audit and credential scanning. The private unpublished project itself is not audited by PyPI. Exact hosted results/head/run are recorded in the PR after final-head verification. No live census or actual per-fund classification percentage is reported.

## Corrected re-entry and definition versions

Self-review of initial head `76783bb` found that the identical-input branch trusted the head hash before checking persisted data. A regression executing the actual publisher seam failed with `DID NOT RAISE` against that head when the projection was empty. Corrected publication and re-entry share the same five data readbacks; missing/changed current or historical evidence produces a stable error without another mutation. Hosted-native regressions damage and reconcile only derived fixture state to check this behavior. A second correction makes `(policy_version,dimension,value)` the definition uniqueness key; a fixture seeds an older version with the same label and verifies both records survive publication. Corrected full local verification passed all 26 checks: 5,960 non-PG tests, 38 subtests and 592 PG deselections, all 21 contract exports, lint, third-party audit and credentials (405.05 seconds). The original head/CI are historical and not final acceptance evidence. Hosted results remain a separate gate.

## Next slice

After review, TAG02 should add the versioned semantic taxonomy/batch/filtering interface and TI presentation against this read model. Before a separately reviewed DEV installation/backfill, inspect the actual role grants, identity collision census and measured operation size/duration. The current sprint authorizes no live installation, grants, role provisioning or runtime activation. No additional CI framework, preservation machinery, copied controller family or dependency change is required.
