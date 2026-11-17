# SCH44 production link review

The offline `nse-sch44-production-link-review-v1` report records candidate
NSE-to-AMFI links and coverage. It leaves the existing SCH43 and SCH44
publisher, crosswalk, and capture workflow unchanged. Run it with retained local
files whose production and AMFI provenance has already been checked. The
command hashes each input and records that hash alongside the witness line
numbers. The report contains private source identifiers.

```sh
umask 077
uv run python -m mfdataindia.nse.sch44_reconcile \
  --source /private/synthetic/current-sch44.psv \
  --catalogue /private/synthetic/catalogue.json \
  --earlier-source /private/synthetic/earlier-sch44.psv \
  --amfi-first /private/synthetic/earlier-navall.txt \
  --amfi-second /private/synthetic/later-navall.txt > /private/synthetic/link-review.json
```

A proposed link requires one catalogue AMFI code and slot for the ISIN,
unambiguous normalized issuer correspondence, a unique AMC, NSE scheme-code
and ISIN key in both SCH44 exports, and explicit AMFI plan and option evidence
in both AMFI files. The AMFI files must agree with the catalogue on both ISIN
slots and with each other on option periodicity. Names of funds are ignored.
Unverified issuer aliases, competing keys and missing explicit variants stay
unresolved. Verified Direct variants remain outside the Regular projection.

The literal `source_reinvestment_flag` and verified `amfi_isin_slot` are
separate. A unique catalogue candidate may have a `candidate_slot`, but only
a corroborated identity gets a verified slot. A Y/N disagreement carries
`SOURCE_FLAG_SLOT_DISAGREEMENT`, including when the variant is resolved.
The flag and marker cannot confer a transaction capability. Effective date
and full member scope remain unverified in this report.

The private 17 November replay against v86 counted 11,976 exact in scope,
167 held, 1,716 unmatched and 17 verified out of scope. Of 1,713 flag holds,
1,546 had enough independent identity evidence. This includes one Direct
variant. None of the 532 unmatched rows with a valid ISIN absent from v86
appeared in the two retained AMFI files. Coverage is for that input only.
The original exports and row-level review delta are kept separately from source control.

## Consumer boundary

The current TLAM purchase route `resolveActiveNsePurchaseRoute` consumes a
promoted SCH snapshot and checks purchase permission, AMC activity, non-demat
mode, minimum and amount multiple before selecting the route. TLAM's
`mapNseSipRule` also requires a promoted SCH row plus exact SIP rule identity
and source eligibility. The link report alone cannot replace the promoted
source in either operation.

For identity review or a read-only join, the smallest delivery is a
versioned private link artifact, with source and catalogue hashes, exact
AMC/scheme/ISIN to AMFI-code/slot links, and the total, exact, held, unmatched
and excluded counts. Report missing identity as unknown. Omit transaction
eligibility from this artifact. Consumer publication and review of the
remaining rows are separate work. Full snapshot replacement still requires
verified NSE effective date and member scope and all existing publisher checks.
