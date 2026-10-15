# IDCW certified-window pilot: HDFC Flexi Cap Regular 101763

## Approved scope and delivery gates

User approved planning and implementation on 2026-10-15. Target one fund and
one proposed window, **2025-04-01 through 2026-03-31**, before expanding.
Preserve all NAVs, identities, published observations, policies and old results.
No UI, production, raw-data correction, inferred payout or blanket unlock.

1. **Acquire and reconcile original evidence.** Retain the date-filtered issuer
   history export and its visible query state, issuer scheme documents, and
   original AMFI event-date NAV response. Check exact Regular identity/ISINs,
   dates, per-unit amounts, full returned row set and old snapshot lineage.
   Track provider-listing scope separately from complete economic-event coverage.
2. **Admit a genuinely complete window.** Establish historical listing bounds,
   pagination/truncation behavior, corrections/addenda and unit-event coverage.
   Obtain applicable reinvestment date/NAV and explicit scheme-level levy basis.
   Document gross research versus actual investor/tax/stamp-duty assumptions.
   Neither a source URL nor an operator assertion can certify these facts.
3. **Implement deterministic total returns once inputs qualify.** Store immutable
   policy/formulas/precision/event ordering and input/code lineage. Generate a
   separate wealth index; retain all raw NAVs and old results. Verify independent
   no-event, repeated-event, delayed-reinvestment, unit-change, missing/conflicting
   evidence and replay cases. No Growth substitution or NAV-drop inference.
4. **Certify and publish privately.** New optional sidecars/restricted roles and
   additive routes only after source acceptance. Fresh backup/new restored clone,
   exact replay, private old/new HTTP checks, actual traffic recovery/restoration,
   effective privileges and final preservation. Unlock only certified
   fund/window/metric combinations; changes invalidate selection, not history.

## Current implementation boundary

The first implementation is an **offline source-bound readiness verifier**, not
a total-return engine, database migration, new API or deployment. It reconciles
the actual historical CSV/query capture, the immutable v2 observation and AMFI
NAV rows, and extracts narrowly identified issuer document wording. It emits
specific remaining gaps with policy/code/input hashes and exact replay.
It cannot emit accepted coverage or eligibility; its successful diagnostic exit
is **2 (blocked)**. Invalid source/identity/hash/layout exits 1, not blocked PASS.

The retained historical export is named `Report_Latest_IDCW` by the issuer even
for historical queries. Filename alone proves no date scope. Capture filters
and the full displayed/exported row set; keep unsupported pagination or amendment
history unknown. A browser DOM capture is operator-observed context, not an
original server response or independently trusted completeness certificate.

On 2026-10-15, the selected issuer query returned one Regular and one Direct row,
each 2026-03-05 at INR 7.0000 per unit. Only Regular is the pilot. The original
AMFI history reports Regular NAV **79.808 on March 5** and **71.772 on March 6**;
the first equals the already reported cum-IDCW NAV. Do not call either value a
reinvestment price or infer a levy from the difference. SID wording about
record-date close-of-business NAV adjustment is not an actual allotment price.
Generic stamp-duty rules are not a quantified scheme levy or investor outcome.

Do not deploy a validation-only schema/profile or modify the accepted MF-E09B
unknown-coverage policy to make these source gates pass. If evidence remains
unavailable, merge only the useful offline verifier/source checkpoint, report
the uncompleted analytics goal explicitly and retain the source dependencies.

## Implemented source checkpoint — 2026-10-15

`scripts/check_idcw_window_readiness.py` now performs this offline reconciliation.
The [retained report](../evidence/idcw-window-readiness-20261015.json) is explicitly
**blocked**, not accepted coverage or a DEV certificate. Original downloads and
capture receipts are retained privately; no original PDF or bulk NAV response is
committed. Two executions against identical inputs produced byte-identical
reports, hash `0cbf5f09b9b8d3899fe3b77268a30333493a73f85ebbd4e5d2d2f4baf5b276c7`.

Sources are an actual issuer CSV/browser-query capture, the retained v2 snapshot
`9dd0520525891277bda573f581861ccfbfe8e7574873a6467a321dce7ac43415`,
an original two-day AMFI response, the November 21 2025 SID (PDF pages 2/57),
and June 2025 SAI with August addendum (PDF pages 1/167). The relevant SID/SAI
pages were rendered and visually inspected. The CSV, SID, SAI and AMFI hashes
are in the report. Browser context is labelled operator-observed, not an original
server payload. No site restriction or CAPTCHA was bypassed.

The verifier checks all six hashes, exact identity/policy/code/snapshot/window,
CSV headers/units/dates, the full displayed/exported row set, duplicate or changed
observations and AMFI rows, and narrow dated PDF anchors. It preserves conflicting
amounts rather than choosing one. The reported stamp-duty percentage is a rule
observation only, never converted into a scheme levy or applied investor result.
Dates are parsed without host-language/locale dependence.

```sh
uv run python scripts/check_idcw_window_readiness.py \
  --manifest /private/pilot/request.json \
  --source-dir /private/pilot \
  --output /private/pilot/new-readiness-report.json
```

The source directory must be operator-owned/private. Requests and sources must
be bounded regular files, not symlinks. Outputs are exclusive-create, mode 0600;
replay writes a separate file, never replaces previous evidence. Exit 2 means
valid **blocked diagnostic**, never success of the analytics goal. Exit 1 means
invalid inputs or failed source validation. There is no acceptance flag, writer,
network fetch, database operation or public/private HTTP route in this tool.

Focused validation passed **119 cases**, including 46 new readiness cases and
unchanged catalog/observation/discovery tests (25 PostgreSQL cases excluded in
that focused run). The full local non-database suite passed **1,053 cases**
(238 PostgreSQL cases deliberately excluded). All frozen contracts, CI-scoped
lint, whitespace checks and dependency audit passed. Full PostgreSQL/image CI
must pass before merge. Synthetic tests do not establish issuer truth or live
acceptance.

### Remaining acceptance work

1. Verify provider listing completeness, truncation/pagination and historical
   amendments for the window. A corroborating filtered export does not itself
   prove the complete economic-event calendar.
2. Obtain the event's applicable reinvestment date and NAV. The original AMFI
   observations are dated NAV facts only; no price is selected from their drop.
3. Establish scheme levy treatment, unit-event coverage and applicable amendment
   history. Do not confuse reinvestment stamp duty or investor TDS with a scheme
   levy; gross research policy and actual investor simulation remain distinct.
4. Recheck live identity during any eventual admission; the offline snapshot
   comparison is not current database certification.
5. Only then implement/certify the deterministic total-return engine and private
   publication with fresh preservation-first operational gates.

No migration, IAM, scheduler, ingestion, runtime or source data changes are part
of this checkpoint. No deployment or IDCW unlock is claimed. MF-E09B continues
serving its accepted reported observations with coverage unknown.
