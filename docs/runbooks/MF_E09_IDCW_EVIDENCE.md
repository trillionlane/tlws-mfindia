# MF-E09 — Authoritative IDCW event evidence pilot

## Status and finite result

**In progress, source gate blocked. This is not completed E09, accepted event
publication, a new API or a DEV deployment.** The offline verifier now produces a
deterministic, source-bound discovery report. That is useful implementation
progress, not the sprint acceptance result.

The original sprint remains: accepted additive distribution-event sidecars and
proven coverage for HDFC Flexi Cap Fund Regular IDCW, AMFI **101763**, over one
finite explicitly supported window. Proposed initial window: **2025-04-01 through
2026-03-31**. No arbitrary expansion to Direct, Growth, all AMCs or full history.

Follow [the representation policy](../DATA_REPRESENTATION_POLICY.md). No data
deletion/correction, wholesale model changes, UI, production, blanket IDCW unlock
or invented inputs. E10 math and E11 eligibility remain later releases.

## Plan and acceptance gates

1. **Source/identity/coverage gate (current work).** Retain original official
   bytes, retrieval evidence and hashes. Compare the exact issuer code/plan/option
   and payout/reinvestment ISINs with current provider identity. Obtain an explicit
   complete record-date listing for the proposed window; resolve pagination,
   truncation, date bounds and any conflicting/replaced entries. Summary factsheet
   rows and a notice alone do not establish no other events. Obtain the applicable
   event/reinvestment date and price evidence, documented unit-event coverage and
   scheme-level levy treatment. Preserve unknowns; no inferred zeroes or NAV-drop
   detection. Reconcile announcements against subsequent reported actual amounts.
2. **Accepted evidence implementation, only after that gate.** Use separate
   append-only sources/events/window snapshots and an immutable stored policy;
   bind exact identity, every source/page/hash, dates, decimal amounts/units,
   completeness proof and code version. Retain superseded/conflicting evidence.
   Same inputs replay identically; changed inputs create new snapshots. Explicitly
   revoke production-style inherited runtime defaults before granting only the
   known append/read roles. No source-table writes, HTTP writer or flag proliferation.
   Add bounded private metadata/event/coverage reads to the current 26-pattern
   profile without changing old paths/components. Withhold conflict/stale identity
   and uncertified windows rather than choosing latest ingestion order.
3. **Preservation-first DEV acceptance.** Fresh backup, a new restored-clone
   rehearsal, source/prior-layer preservation including MF-E08 overlap, actual
   least-privilege checks, bounded append/replay, old/new route certification,
   public denial and actual traffic recovery/restoration. Independent verification
   uses original issuer evidence, not the production parser as its own oracle.
   Record exact merged/runtime SHA/images, certificate scope and remaining gaps.

Each stage is gated by its predecessor. Do not migrate/deploy a validation-only
schema/profile or call discovery a finite sprint completion. The current PR is a
source-gate checkpoint; finishing E09 requires the source-dependent remainder.

## Current real-source observations — 2026-10-15

Original documents retained privately; none committed or republished:

[Sealed discovery observations](../evidence/mf-e09-source-gate-20261015.json)
record this source gate as **blocked**, not an acceptance certificate. Two
executions against the same original bytes/request produced identical reports.

| Role | Official source | SHA-256 / selected pages |
| --- | --- | --- |
| Issuer identity | [September scheme summary](https://files.hdfcfund.com/s3fs-public/scheme_summary/2026-09/HDFCEQ_1789454586.pdf) | `884ef54c8e0ffb4097652cccbbf72f863e8f45ee319064cf85e9b0d41d1eb3e3`; page 1 |
| Reported history | [April 2026 factsheet](https://files.hdfcfund.com/s3fs-public/2026-05/HDFC%20MF%20Factsheet%20-%20April%202026.pdf) | `98fb01d8c76481e205958440d265ae924454d01f2042b1050d624303efa76bcd`; cover 1, history 121 |
| Conditional announcement | [March 2026 record-date notice](https://files.hdfcfund.com/s3fs-public/2026-03/2058%20-%20Notice%20-%20IDCW%20Record%20Date%20-%20HDFC%20Flexi%20Cap%20Fund%2C%20HDFC%20Multi%20Cap%20Fund%2C%20HDFC%20Small%20Cap%20Fund%20-RD%20March%2005%2C%202026.pdf) | `5ef0eb7cb0de622cd39679e32c8f7c3da16581f40bb24e6da7a63608ec18634f`; page 1 |

The issuer summary binds 101763 to Regular IDCW, payout ISIN `INF179K01582`
and reinvestment ISIN `INF179K01590`. A read-only comparison with the retained
E08 restored clone matched those identifiers. This is **not** current live DB
identity certification; the discovery report explicitly retains that blocker.

The exact fund's history reports INR 7 per unit for record dates 2024-03-14,
2025-03-13 and 2026-03-05. One reported row falls inside the proposed window.
That count is not complete-window proof. The March notice states a conditional
maximum of INR 7, not an unconditional final distribution. It also publishes a
February 27 reference NAV; that is not a reinvestment price. Generic statutory
levy/TDS/stamp-duty language does not quantify the scheme/investor amounts.

The summary's "Past 3 years" heading does not establish an exact start/end or a
non-truncated listing. The same page's Value Fund block includes a 2022 event;
do not turn the heading into a machine-certified calendar interval. The site
points to its record-date listing. That listing has not been captured/proven:
the bounded automated HTML request failed and the browser did not yield a usable
history table. [AMFI's scheme-distribution selector](https://www.amfiindia.com/otherdata/scheme-dividends)
is another official discovery route, not retained accepted evidence here.

**Remaining blockers:** complete event listing/window, applicable reinvestment
date/NAV, quantified or explicitly justified scheme levy/unit-event coverage,
current live identity certification. Acquisition of facts does not establish
these missing facts. No source permission calling exercise is being reintroduced.

## Offline source-gate tool

`scripts/discover_idcw_evidence.py` has no network, DB or publication command.
It accepts a strict `IdcwDiscoveryRequest`: exact identity, proposed start/end,
history period, three separate source roles (history/notice/registry), each with
local PDF basename, official source URL, expected SHA-256, timezone-aware
observation and selected original page numbers. History uses cover plus table;
notice and issuer registry use page 1. Record reported authorization separately
from source evidence; do not claim a permission letter or endorsement.
The notice adapter is deliberately pinned to the March 1, 2026 / March 5 record-
date layout; it is not a general notice parser or full-history acquisition flow.

Run against an operator-owned private directory containing the original PDFs:

```sh
uv run python scripts/discover_idcw_evidence.py \
  --manifest /absolute/private/request.json \
  --source-dir /absolute/private \
  --output /absolute/private/discovery-report.json
```

Exit **2** means a valid discovery report with publication **BLOCKED**. Exit 1
means invalid inputs, failed decoding or an output error, not a successful report.
No mode currently returns accepted events or a passing completion certificate.
Outputs are exclusive-create, mode 0600; they cannot replace source or old report
files. Reject linked source files, oversized inputs, wrong hashes, encrypted PDFs,
ambiguous identity/columns and unparsed event rows. Source URL fields are offline
provenance metadata, never passed to an environment-variable assignment or fetch.

The new isolated decoder supports **16 MiB** (the real factsheet is 9,133,440
bytes), 400 document pages but at most two explicitly selected pages, a 30-second
wall/20-second CPU budget, 512 MiB Linux address space and bounded streams/text.
No OCR, decryption or provider-block bypass. The old 8 MiB PDF policy, hashes,
published artifacts and acquisition rules are unchanged. New policy and exact
parser/worker/helper hashes are included in each report; they are not installed
in the DB until an accepted publication feature is approved and implemented.

Hermetic tests are labelled synthetic and cannot certify issuer data, IAM or DEV.
Real-source report execution/replay is separately recorded in the PR. A report
hash proves integrity/replay only, not completeness or a trusted certificate.
