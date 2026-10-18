# FS-08–FS-11: actual DEV closeout and backend handover

Recorded 2026-10-18 after the FS-11B coordinator exited **zero**, verified its
sealed acceptance and completed the additional actual core-values witness.
This records actual DEV evidence, not a proposal or CI-only result. The failed
A attempt, its backup and its evidence remain intact. This docs-only change
does not deploy an image or change serving traffic.

## Exact release and serving boundaries

- Source/review SHA: `1d1311cbe17e32493a47a6ab7a4fcd6eb7fdefb3`
  ([PR #94](https://github.com/trillionlane/tlws-mfindia/pull/94), following
  [PR #93](https://github.com/trillionlane/tlws-mfindia/pull/93)). Reviewed head
  and squash merge have identical tree `bc124cddffa95bebed4e93949d6afce52c868e1e`.
- APP: `asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia@sha256:a89b46b837d3c0cfcc60774b50d429d0bdc2e09f05380dc84afa947fc40afbc6`.
- Restore/tools: `asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia-review-tools@sha256:219fbe7112c950d7c42961bf09c7004b21b6389c047feb4fed0c5e8d2d0df92e`.
- Both images independently passed manifest/config-digest, Linux/amd64 and exact
  OCI source-SHA verification. Source configuration v4:
  `6fb6c0c804a1dac20e4e2c4aa25f3cd107ce7c58517164409ae2da52e97869d4`;
  policy v8: `2b1d478498a72a267ecd4719300a5456db52fa0764b2a446e9ee75b545839e5a`.
- Review revision `tlws-mf-review-dev-dev-uti-261018b-books` serves 100% of its
  service traffic on that APP. The six-task source job uses the same APP.
- Protected core **was not redeployed**: PR #89 SHA
  `2f173e4abc4b05e2c4c20c69d96029c29646373d`, revision
  `tlws-mf-data-dev-dev-fs09-page-261017a`, digest
  `2b8de1ab8d5cccce81df8bcabbcadc0cb3b83f07299eed4e098e48590c0fb0c2`.
  Its existing SQL projection reads the accepted additive facts. Neither the
  source/review SHA nor this documentation's SHA is its serving SHA.
- Exact-head [Verify 39086219734](https://github.com/trillionlane/tlws-mfindia/actions/runs/39086219734)
  and full-main [Verify 39087760797](https://github.com/trillionlane/tlws-mfindia/actions/runs/39087760797)
  passed **1,939 tests, one skip**, audit, image builds and worker/PDF smokes.
  [Image-only preparation 39087768324](https://github.com/trillionlane/tlws-mfindia/actions/runs/39087768324)
  succeeded with live execution skipped. Generic deployment
  [39087760763](https://github.com/trillionlane/tlws-mfindia/actions/runs/39087760763)
  was skipped; repository auto-deployment remained `false`.

## Real rollout evidence

Local operational ledger: `artifacts/mf-fs11b-dev-20261018-tEAekX/` (not checked-in
raw source or credentials). Manifest:
`01709b298836e16210a638bfe2491fc3137b16caf2516f912170927d5df5e940`.
Final `22-acceptance.json` seal:
`5851be0e2a218ec11a5d6ebf92c1b0fda73039b2efa31e2451d092957268dc69`.
All sixteen original sequential gates passed; current graph generation is 25.

| Actual gate | Immutable execution / evidence |
| --- | --- |
| Genuine pre-rollout normal book run | `tlws-mf-official-facts-refresh-dev-9kkvw`, 03:30:05–03:41:09 UTC, all five prior tasks succeeded |
| Fresh exact-image restored clone | `mf-book-clone-dev-uti-261018b-mq9vs`; seal `2d5b3bae6cfc2eafd812caa419bef566aa734cfc7fb3de83425f481f2ac0343a` |
| Manual six-task source run | `tlws-mf-official-facts-refresh-dev-l4fkg`, 04:22:19–04:37:45 UTC, six successes, zero retries/failures |
| Actual prior graph/source/traffic recovery and replay | `12-recover` seal `36ec77f94098a98c1d1659b43f805dd2c7d506fcd6eac7e1ab760423b83e6a7a`; replay `86d91dc11e0459649527af6d5ab9382f692041958ab9101d2f61da56b5a5193d`; old-source private HTTP passed |
| Reinstallation / reactivation / restored private HTTP | `14-reinstall` seal `6219a6f3bcb585b71a5852bfcbe98b2391466547876be0532a9daa2062a70a19`; `15-restored` seal `627db0b80564bfa6e970195788cf911ecd2b4e5c5d23045e5fbd246f1388caf5` |
| Genuine Scheduler-created six-task run | `tlws-mf-official-facts-refresh-dev-vwsrv`, 04:59:05–05:15:01 UTC, six successes, zero retries/failures |
| Exact scheduled receipts / every archived original | `17-scheduled-source` seal `1ad797a6cae6100233ed6d26d011b79e57c4d2df0a85e3872ae0b56105828629` |
| Genuine Scheduler attempt | seal `912a46bfa9013e4e5423bdc651b3a47c2b8e2289c183d161cb0f7adf2b58481d`; one AttemptStarted and successful AttemptFinished, not manual invocation |
| Scheduled-source private HTTP recertification | `18-scheduled-http` seal `e0b4ba496e1363bc0be7126c7fef12a195dcbc2392aa784bd7b8a573704ca456` |
| All 19 private core templates / 22 responses | `19-final-core` seal `7e9536e62c5a88b28488adade5a0065f2b35aa8ac63c88734e5c4cdd3b5ff133`; dataset 27, Growth-first eligibility, IDCW exclusion and explicit unassessed `signals: null` |

The timer execution's creator is the existing
`tlws-mf-data-scheduler-dev@trillionlane-dev.iam.gserviceaccount.com`.
The ordinary **09:00 Asia/Kolkata** daily schedule was restored and independently
read back enabled. Normal 06:30 NAV / 06:40 audit UTC windows were unchanged.
Genuine 00:30 NAV `tlws-mf-data-nav-refresh-dev-jvxc2` and 00:40 audit
`tlws-mf-data-nav-audit-dev-ksk6d` were reconciled with application results;
dataset 27 was observed, not assumed. These older normal job images have no
OCI Git revision label: source provenance remains **unknown**, with exact
unchanged image/config/application evidence retained. This does not relax
strict provenance for either new UTI image.

## Preservation and independent evidence

Fresh B backup: `gs://tlws_mf_data_source/verified-review-rollouts/dev-uti-pre-261018b/snapshot.dump`,
generation `1792295483740680`, 196,071,170 bytes, SHA-256
`6b0129f166405c9f1f47dc5c31161001d4c92709adb013c6ec845073c9009c9c`.
After-backup: `gs://tlws_mf_data_source/verified-review-rollouts/dev-uti-post-261018b/snapshot.dump`,
generation `1792301003445694`, 232,555,234 bytes, SHA-256
`3a77f3c4e5a52c0e84d7e5060d0c6f64b1592ef87d1e72b886f4950b4c978e69`.
The larger after-backup includes additive source/evidence records, not raw edits.

All **14,405 original funds and 4,248,810 original NAV rows** were preserved.
Every one of 497 pre-existing rows across 38 prior-layer relations retained its
exact seal; final population is 656 rows across those relations. Before-layer
seal `cb4001c5e71760976094197b4d898cced188023d9d8a9e7164bc3b41c6ac529e`;
final-layer seal `5dc656d3f3c87f6b7333489d9beaf1334be1006e0904cac362a20551ac2fa9f0`.
The **entire** prior-AMC projection matched, not just examples. Human-review
ledgers were unchanged; zero human events and zero source-row corrections.

The restored clone additionally compared **all 1,847 prior-AMC codes across
the four original pre-repair queries**, with full-population equivalence.
The complete current-facts facade comparison covered 14,385 rows: a different
population from the 14,405 original funds. No resource, timeout, source-policy
or preservation waiver was used. Actual-source clone recovery and later live
recovery/replay/reactivation both passed. The
[failed A / read-cost repair record](FS11_RESTORED_CLONE_READ_COST_2026-10-18.md)
remains retained; A was never counted as accepted or reused as B's backup.

All six task receipts and every listed archived original were checked against
actual generation, bytes, SHA-256 and MD5. UTI has **67 verified archive proofs**,
including four English Active/Passive PDFs across both months. Four independent
book/period raw-source oracles were checked, including current Growth/IDCW
identities and separately labelled TER references for all four books. Matching
API/database output alone did not substitute for independent source evidence.

## Actual coverage, not all-AMC or all-fields completeness

These are retained **scheme-variant** denominators, not fund-family counts.
Usable means supported usable evidence, not every field or human verification.
Every population matched its latest successful six-task source receipt.

| Source | Reporting date | Retained denominator | Usable | Explicit gaps |
| --- | --- | ---: | ---: | ---: |
| ABSL | 2026-09-30 | 192 | 174 | 18 |
| ABSL | 2026-08-31 | 192 | 146 | 46 |
| SBI | 2026-09-30 | 258 | 115 | 143 |
| SBI | 2026-08-31 | 258 | 108 | 150 |
| Kotak | 2026-09-30 | 205 | 41 | 164 |
| Kotak | 2026-07-31 | 205 | 2 | 203 |
| UTI | 2026-09-30 | 208 | 80 | 128 |
| UTI | 2026-08-31 | 208 | 80 | 128 |

Kotak August remains missing. Its reviewed-known-document acquisition still
reports live catalogue discovery unavailable and expires before 2026-11-01 UTC;
acceptance neither removes that warning nor permits bypassing it. The retained
HDFC proof remains a narrow pilot, not whole-HDFC coverage.

| UTI accepted field observations | September | August |
| --- | ---: | ---: |
| Explicit-date closing AUM | 78 | 78 |
| Benchmark | 80 | 80 |
| Complete manager block | 80 | 80 |
| Matching-date same-fund risk | 4 | 0 |
| Supplemental Regular TER, **not BER** | 78 | 78 |
| UTI-sourced BER overrides | 0 | 0 |

For each UTI month the 128 gaps comprise 48 missing exact code/ISIN bridges,
40 variant-identity conflicts, two family conflicts, 15 missing/ambiguous monthly
headings and 23 unsupported current identities. No fuzzy match/date inference
cleared them. Reporting dates come from actual `Portfolio as on` evidence
(2026-09-30 and 2026-08-31), not edition, URL or filename dates.

## Additional actual core target-values witness

After full acceptance, `mf-core-facts-dev-uti-261018b-a-8dw9h` checked seven
independent references through **14 real detail/batch responses**: UTI 100669,
100668, 100822, 100823 and prior SBI 103215, 103216, 102272. All supported
original-source target values matched; every response was `no-store` and took
**0.144–0.244 seconds**, below five seconds. Detail responses validated v2
research objects. Compact batch responses intentionally lack detail-only v2
quality objects.

Certificate `ec00614c47b44cca4252322ac8f1472cca8e2bf0dd5dbfae5183360fb6dbf5f7`
binds the source acceptance above and independent reference hash
`9d06044437042181e0ed70d0957f063063086fb4c164490899780e54efe4b3b9`.
Anonymous core access returned 403. The witness mounted no database credential,
made zero data writes and checked protected core/NAV/audit runtime unchanged
before/after. This is backend HTTP evidence, not UI testing or a population-wide
latency benchmark. TER remains separately source-certified, never BER.

## Consumer readiness and remaining work

The existing private core and three v9 reads plus three retained v8 compatibility
reads are ready for **existing authorized backend consumers** within the certified
scope. See the [consumer handover](../OFFICIAL_FACTS_CONSUMER_HANDOVER.md#fs-08fs-11-additive-book-coverage)
for origin/audience/assertions/scopes, no-store, per-field dates and gaps. The
52-path profile is not a claim that 52 new routes were live-tested. Source v8
compatibility is unrelated to Insights' strict core v2 contract.

Observations remain **`needs_verification`**; no human review was invented.
TER is not BER; adjusted IDCW analytics remain disabled. Raw records, canonical
calculations and historical originals were not rewritten. Remaining AMC cohorts,
missing identity/month evidence and field-depth adapters stay on the
[roadmap](../ALL_AMC_FACTS_EXPANSION.md). No UI or production work is included;
neither all-AMC nor whole-factsheet completeness is asserted.
