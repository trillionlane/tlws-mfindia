# AMC loading: implementation record

Base: `7856eeb38decc2e1b64868cc5bbd12bccba66643`.

The first repair is Kotak. The retained 24,740,205-byte original has SHA-256
`105e49dfb9787698644810d13a01065bae940f18a80d8c60f082ef71f706dda2`.
It reports 31 October 2026 inside the PDF. The deployed adapter plus pypdf
6.21.4 extracts 333 pages but recognizes zero detail pages. The issuer replaced
its Issuer/Instrument layout with Fund Details / Fund Performance panels.
An actual-panel regression failed on the base. The versioned successor currently
recognizes 94 fund-detail pages locally, without new provider acquisition.

The successor keeps direct AMFI code/ISIN/name identity matching, actual source
dates, explicit AUM units, Regular BER, benchmark whitelist, and field gaps.
Adjacent same-month expense-table conflicts are withheld. No NAV, AAUM, Direct
expense, URL date or guessed benchmark is substituted. Original v1 remains.
Actual text and original hashes are retained; fixture panels are excerpts from
that original, not synthetic expected values. This is local parser evidence,
not native database or live publication evidence.

Current composition must bind the new method explicitly while retaining the
installed v4 UTI method and all old source authorities. No default-v5 activation
or extension of the expired Kotak fallback is permitted. New publications must
retain September evidence and keep the old source contracts independently
recognizable. Historical numbered SQL and consumed attempts stay immutable.

The shared gate follows this repair: registered source/actual period/document
hashes/derivation version form the unique claim; committed identical inputs
skip derivation, revised bytes form a new candidate, and unresolved effects
block retries. Discovery state is distinct from publication state. Waiting or
incomplete inputs preserve the last valid period. Each source owns its own
claim/transaction; a failed source does not block another.

All 57 retained census entries remain visible, including unsupported, unmatched
and ambiguous identities. The 16 October census is dated evidence, not a new
AMFI refresh. Only sources with a working adapter and publication path run.
UTI requires the complete issuer-linked English Active/Passive pair with actual
PDF dates. HDFC's pinned legacy edition and Small-ABSL's conflict need separate
reviewed corrections; neither is silently enabled by the Kotak parser repair.

No schedule increase until hosted disposable-PostgreSQL chain tests prove
empty/incomplete/published/unchanged, concurrent delivery and source isolation.
No completed reader cutover or dataset integration changes are included.

## Validation and intentional compatibility

The actual-panel test failed against the base before the correction. Final local
validation passed 5,809 non-PostgreSQL tests and 38 subtests; 579 native cases
were deselected locally. All 21 OpenAPI exports, lint, dependency audit,
credential scanning and diff checks passed. This does not substitute for hosted
native/both-image CI. The private unpublished package itself is not on PyPI and
is not audited; there is no waiver of a dependency finding.

Four historical constructor files have explicit whole-byte inverse recipes.
They preserve the exact v1–v5 source configurations/policies and original
allocation descriptor. The current source reference is a new document; the
prior authority documents and numbered migrations are unchanged. Separate
current-source tests reject unrecognized modified bytes. No assertions or test
IDs were removed to permit the new source method. The old default stays v5;
the current process explicitly selects retained-v4-plus-Kotak-layout registry 6.
UTI remains on its installed v1 method, not the dormant default-v5 method.

The owner-only method installation requires the exact active v4 book/current
configuration. It reuses the v4 seal function. Its view change adds only exact
retained specification membership; all financial, source and identity SQL
expressions restore byte-for-byte to the prior projection. It retains prior
batches and September selection while admitting new-method batches. The
read-only dataset query path is also exercised in the new native regression.

The existing six-task process retains its source mapping. The explicit
`--kotak-only` mode accepts exactly one actual Cloud Run task at index zero and
maps it to the fixed Kotak source. Six-task input in this mode is rejected. It
is not a claim that the normal six-task command is targeted. Both modes require
the same fixed DEV job/database/generator and unchanged source/resource bounds.
The packaged smoke now exercises the actual current main and an isolated
synthetic PDF through the real decoder and layout selector in both images.

## Release/recovery boundary

This code is not a completed DEV recovery. Before the source configuration is
installed, all serving consumers of that configuration must run the compatible
certified release; the generator must use the current allocated entrypoint.
The core reader connection, TI configuration and source schedules remain intact.
Prepare exact current resource/image bindings after independent review and MAIN
certification. Reconcile prior publication and active generator sessions before
one distinct targeted recovery. Do not replay the failed execution or execute
the full six-task job while describing it as Kotak-only. Stop on uncertain
publication; inspect its immutable identity and fresh database state before any
further write. API verification of October and preservation of prior values are
still required after the reviewed recovery, independently of parser/CI success.

The initial hosted run `42018331023` at `65ec153` remains failed and inadmissible.
Its focused UTI suite passed three cases and failed ten with `KeyError: 6`:
the historical fixture declared a synthetic 1–5 registry but retained the new
registry's key 6. The fixture now advertises only the exact versions it builds;
production registry/version validation is unchanged. The APP smoke additionally
found that a wheel image has no `/app/src`. Current allocation now verifies each
actually imported Python module and any checkout copy, while non-Python files
remain root-bound. Two provider-free image-layout regressions exercise absence
of `src` and rejection of changed installed bytes. Neither correction weakens a
source hash or enables an unallocated process. The original complete failed log
and actual job/step results are retained separately; no rerun or retroactive
acceptance is claimed.

A separate full-original September check used retained generation
`1792252586232266`, SHA-256
`fba08af16c51879d060c867759126b906698a54a2ff237fa7803cac11d760d36`:
all 87 recognized detail pages and every parsed field are exactly equal between
v1 and the successor. This is local source preservation, not a DEV reload.

### Native fixture identity and policy-metadata correction

The next hosted run (42020852872, exact 6d31b1b) passed all image checks,
the 13 focused boundary cases and 6,375 remainder cases, but the new native
case failed at its first run receipt: its synthetic execution name omitted the
fixed job prefix required by the unchanged SQL constraint. One existing skip
remained; downstream audit/credential/process checks did not run after that
failure. The complete original failed log is retained, not rerun or admitted.

The fixture now uses the real job constant for all four publication/replay
calls. Inspection of the remaining path also corrected the policy transition
comparison: all response members remain equal except policy_hash and the
facts_revision that includes that policy hash; both new hashes are explicitly
recomputed and asserted. Values, identities, dates, source evidence, review
states, old batches/receipts and denied mutations remain checked in full.
No production code, SQL, policy, timeout, skip or privilege changed in this
fixture-only correction. Its native result still requires the new exact-head
hosted run; earlier green image/unit results do not certify that path.
