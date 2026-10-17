# FS-09 certification reader hotfix

The merged SBI implementation has **not** been activated in DEV. Its exact-image
restored-clone rehearsal passed, but private candidate compatibility exposed a
real operational checker defect: the restricted reader cannot SELECT the core
`mf.current_fund_facts` facade. That is intentional privilege separation. The
candidate stayed at zero traffic and no SBI schema/source upgrade was started.

The checker now reads the already-granted `mf_live_facts.resolved` view only
after verifying the active facade's exact sealed query, common owner with no
unsealed view options (including invoker execution), and ordered column
signature (name, position, type/modifier and collation). It still hashes every
column of every retained row. The logical core hash key and certificate format
remain unchanged; owner-side direct-facade hashes must match restricted-reader
hashes exactly. Drift fails closed even when the rows happen to be equal.

No migration, source edit, grant, IAM change, extra GitHub variable, public API
or UI work is included. Historical source configurations/policies remain
byte-semantically unchanged. The normal NAV/audit schedules are untouched.

The rollout's compatible-HTTP worker must receive prior core expectations from
an owner-owned **read-only** inspection. It must not call the full core query
engine with the restricted enrichment principal: that engine uses unrelated
core-only relations and sibling fallback. API comparisons still use the real
private core HTTP service and the existing caller identity.

Rebuild exact merged images and repeat the approved fresh-backup, isolated
restored-clone and compatible-image gates. Only then install the restricted
source authority, run all four real source tasks, certify the new raw/canonical
oracles, activate/recover/replay/reactivate, witness a genuine scheduled
execution, certify all 19 core routes and close out original/prior-layer seals.
Keep failed attempts and backups; green CI alone is not DEV acceptance.
