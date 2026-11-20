# Current SCH44 identity rule v2 and route source semantics

`nse-sch44-production-link-review-v2` supersedes only the earlier-export-presence
requirement of the historical v1 link review. NSE scheme codes and AMFI codes are
separate identifier namespaces. A unique current production route can be new.
Keep the current AMC/code/ISIN key unique and require one catalogue code/slot,
corroborated issuer, known explicit AMFI plan/option in two retained AMFI files,
exact slot pairs and compatible option periodicity. An absent historical key is
not adverse evidence; competing retained keys, conflicting retained ISINs and
incompatible source tokens still block. Record an absent earlier line as null,
never fabricate a historical witness. Source names/suffixes do not select a fund.

The source-native candidate records `identity_rule`. Publication rejects an old or
missing rule version or inconsistent evidence hash set before opening a transaction. Its fund-details route adds
`identity_evidence`: rule version, AMFI code/issuer, historical route state and
both AMFI witness hashes/line numbers/periodicity. All 44 literal fields, original
flag, exact AMFI slot and distinct route codes remain separate. No canonical
crosswalk, MFData variant, NAV, tag or family is updated by this rule.

## Pinned revalidation

The production export SHA-256
`407e9642d2cf5a006237268f5912641b998f90a46e52b4ed934ac3ef5cecc2f4`
has 13,876 rows. Catalogue v92 input SHA-256 is
`49ee98261e6d13e6a6c7727d8fc45ddb92b8e2b3fd9814712308cdb7cd35af66`.
The v1 baseline and the separate audit were independently reproduced before this
implementation. V2 adds 143 routes across 59 AMFI variants, with no change to any
of the prior 11,976 exact identities. Final expected dispositions are 12,119 MF
exact, 147 MF held, 1,387 MF unmatched, 165 SIF, 17 other verified out of scope and
41 unclassified: 13,876 total. These are pinned candidate counts, not live data
or fixed runtime thresholds. A different input needs its own reconciliation.

## Production examples versus UAT observations

The sanitized identity subset in tests pins production source lines/fields while
other generated row limits remain synthetic. Production lines 224/225 contain
`B313G-L1` / `B313G`, both ISIN `INF209K01751`, RTA `313G`, with settlement `L1` /
`T1`, respectively. Lines 206/207 contain `B141B-GR-L1` / `B141B-GR`, ISIN
`INF209KC1761`, RTA `141B`, settlements `L1` / `T2`. All have source SIP flag `Y`.
No settlement is inferred from the code suffix or the UAT form.

These Birla rows still lack independently approved correspondence between NSE
`BIRLASUNLIFEMUTUALFUND_MF` and the AMFI issuer. V2 does not guess that alias.
Conservative Hybrid's Regular Growth 101818, Direct Growth 120705, Regular IDCW
101816 and Direct IDCW 120550 remain separate catalogue variants/ISIN slots.
There are no production rows for the two stated Direct Growth/payout ISINs in
this pinned file. The examples remain unlinked in consumer fund details until
all identity evidence is established. Their literal source rows/reasons remain
available in the dataset/reconciliation path. UAT examples cannot override this.

## NSE route authority and SIP support

NSE is authoritative for its route fields and disclosed facility constraints;
AMFI is authoritative for AMFI variant identity and NAV. The retained official
NSE Web File Structure, December 2025, p79, SCH position 29 (`SIP FLAG`) states
`IF 1 THEN Y ELSE N`. The observed SCH44 header at that same position is
`SIP ALLOWED` and the production export uses literal Y/N. Document PDF SHA-256:
`67ee2e34c7cf776a2731e950837bd3aa6f2e6f7e5f521cbdbbce5cc60aae307d`,
already pinned in `NSE01_FIELD_MAPPING_20261110.json`.

`source_sip_allowed` exposes this dated NSE facility declaration: Y=true,
N=false, any unreported/unreviewed token=null. It does not depend on settlement,
a route suffix, source reinvestment flag or a UAT observation. The raw field is
always retained in `fields`. No variant-level boolean collapses different routes.

The section's existing `transaction_eligibility=NOT_ESTABLISHED` refers to complete
order/investor eligibility, not an absence of NSE facility evidence. SCH44 does
not supply the separate SIP master recurrence/frequency/gap rules, mandate and
investor authorization. Those cannot be invented from a UAT form. Other unresolved
field meanings (including additional-purchase maximum and settlement taxonomy)
still retain explicit field holds; consumers must not assign guessed semantics.
No TLAM changes or purchase/SIP submission is included.

## Validation and release boundary

Unit cases cover current-only routes, adverse historical keys, both flag/slot
disagreements, missing/contradictory issuer/plan/option/slot evidence, Direct scope,
the production identity examples and all-field serialization. Hosted native
PostgreSQL proves current-only routes through actual publication, private reader
fund details and paginated authenticated dataset reads; withheld/SIF rows are not
consumer links. Re-entry, replay, changed source revision, actual late corruption
rollback, reader denials and canonical/generator preservation remain exercised.
The existing 5s statement/lock and 15s transaction limits are unchanged.

All private originals, prior packages and failed evidence are retained. A new
candidate hash/storage definition is required for this version. Existing legacy
SCH43/FULL publication and its gates are unchanged. Source-native publication,
new roles/installation, application release, schedule/IAM and TLAM activation
remain outside this implementation authority. PR170 stays open for review.
