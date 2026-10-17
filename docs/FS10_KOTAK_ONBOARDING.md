# FS-10 — Kotak monthly official-facts expansion

Approved with FS-08 through FS-11 on 2026-10-17. This is an implementation
record, not a DEV acceptance certificate. Backend/data only; no UI or PROD.

## Finite result

Onboard one additional source task in the existing private monthly-facts
process. Preserve registry versions 1/2, all prior parser policies and their
hashes, original scheme/NAV/facts tables, every earlier evidence row and prior
AMC projections. Registry v3 and policy v6 add Kotak only. Projection v3 uses
the exact v2 SQL, not a new data model.

The current source is a whole-AMC PDF reached through Kotak's actual public
monthly forms API. Its 32 MiB bounded layout decoder is separate from the
frozen older decoders. The previous available disclosure is a reviewed,
single-fund Nifty 50 one-pager; its **actual July date** is retained despite
its August URL. The missing expected August period remains an explicit gap.
Two available reporting dates do not mean two consecutive full-AMC books.

## Evidence and identity

- Retain the exact forms catalogue, directory, returned AMFI document index,
  XML identity originals, and both PDF originals. PDF/XML archives are
  content-addressed, create-only and generation/hash checked. A redirect or
  challenge is a source failure, not permission to bypass controls.
- The AMFI directory identifies candidate families, not scheme identity or
  the coverage denominator. The denominator is the complete retained current
  in-scope Kotak population, including unsupported/ambiguous variants as gaps.
- A usable fact needs exact code, checksum-valid ISIN, explicit plan/option,
  and a unique source-authored monthly heading or former name. Never repair
  conflicting issuer XML using database guesses or scheme-name similarity.
- Preserve every parsed code claim, including unpaired/unsupported Direct
  claims, for cross-family conflict detection. Daily/weekly/monthly/default
  IDCW and payout/reinvestment are not cross-joined. Missing frequency can
  pair only one unique labelled plan/option code with one unique ISIN.
  SQL checks the pairing basis and retained parent-index/original edges.

## Supported fields and honest omissions

The supported core fields remain benchmark, complete manager block, Regular
BER, closing AUM and explicit scheme risk. AUM is not AAUM. BER is not TER.
Fund risk is not benchmark risk or a riskometer legend. Reporting dates come
from explicit source text, never edition/filename/URL/creation metadata.

The layout text can merge adjacent portfolio columns. A benchmark must be a
complete labelled block, match an immutable reviewed TRI-label policy and
survive SQL validation; contamination and partial/unsupported names remain
unavailable. Explicit Tier 1 can be selected without substituting Tier 2.
This is not an economic benchmark or index-price data feed.

Source context dates and field-effective dates remain distinct. Old values
are not erased when a later field is missing. New official facts can replace
stale current projections through the existing reversible overlay; original
records are never updated. Publications remain `needs_verification`; no human
verification events, adjusted IDCW analytics or whole-factsheet semantic
coverage are fabricated.

Downloaded PDF originals are create-only archived and generation/SHA-verified
before decoding. Later parsing, identity discovery or deadline failures retain
those already archived books in the failed run's evidence, without publishing
facts. An archive failure stops processing without an ambiguous upload retry.
Content hashes prevent double archival in the later XML/originals phase. This
ordering change adds no permissions, source correction or timeout allowance.

## Acceptance gates

1. Full exact-head CI, implementer self-review and normal merge.
2. Fresh backup and exact-image restored-clone rehearsal with actual sources,
   independent visually reviewed canaries, complete prior-row/projection seals,
   private read tests and recovery/replay/reactivation.
3. Stage and certify the new image against the accepted old authority before
   installing the sequential schema/source upgrade. Restricted identities and
   private invoker boundaries must remain unchanged.
4. Run all five source tasks in one actual execution, validate real immutable
   archive generations, source receipts and full population/gap accounting.
5. Certify private extension and all 19 core reads, activate, demonstrate real
   recovery and replay, then reactivate. Observe a genuine Scheduler execution
   and restore the normal daily 09:00 Asia/Kolkata schedule.
6. Take an after-backup and require complete original-data, every prior-layer
   row and whole previous-AMC projection preservation. Keep failed attempts
   and original objects. Never pause/change NAV or audit schedules.

Only then record accepted DEV coverage and move to the separate FS-11 UTI PR.

## Bounded operator references

The actual FS-09 pre-activation run exposed an OS command-size limit when unused
legacy core-detail metadata was embedded in a certification request. The
owner inspector now returns only the supported fact aliases consumed by the
core comparison, using the real API query and its sibling fallback. Missing
facts remain explicit nulls. Full original-table and entire prior-projection
row seals remain unchanged; no data, comparison field or database grant is
removed. The transport retry and failed attempt remain separately recorded.

The separate FS-09 core-read-cost hotfixes (#86, #87 and #88) are rollout prerequisites.
Their single-pass scalar expansion and equivalent predicate-friendly facade are
also used by v3; the dated field-selection policy and v2 field-versions SQL
remain unchanged. The bounded batch lookup resolves identifiers before querying
facts; it must not evaluate every fund's facts for a small requested cohort.
The final scalar-bounded lateral facts and sibling reads must remain intact:
the array-only predicate was still slow with the expanded SBI overlay. Resolve
identifiers first and retain the fixed projection, bound identifiers and
anti-flattening boundary without changing eligibility or response semantics.
The exact-image rehearsal must check the actual core HTTP
detail/batch paths as well as metadata, then all 19 core routes. Increasing
client timeouts is not an acceptance substitute.

## Read-only available-period preview, not DEV acceptance

The 2026-10-17 retained-original preview covered the complete 205-variant local
cohort: 41 usable September observations and two July observations. September's
164 gaps comprise 116 missing exact bridges, 14 identity conflicts, seven
unavailable-field results, 24 missing/ambiguous headings and three unsupported
identities. The July source is the Nifty 50 one-pager, not a whole-AMC book;
August is still explicitly missing. No source or matching policy was weakened
to turn those gaps into usable or human-verified data. These are preview counts,
not final-image source publication, live certification or all-AMC completeness.
