# CI-only source evidence: actual attempt provenance

Base: `69037c0ec078ad67a8dfd708bc3cf86040b655e6` (merged PR136 and PR137).
This is a narrowly scoped verification prerequisite, not RO-02 completion,
live reader enablement, or authority to merge a third PR.

## Reproduced failure

Main Verify `41202440519` attempt1 failed 61 historical byte checks because
the original reviewed PR136 source commit was not reachable after squash and
automatic source-branch deletion. A new non-release reference to that exact
commit repaired cold checkout reachability; 152 targeted cold-checkout tests
passed. No reviewed source bytes were substituted.

Attempt2 eliminated those 61 failures but remained FAILURE: 13 focused passes,
5834 remainder passes, 3 failures and one existing skip. The three native source
collectors completed their database work, then their CI-only wrapper rejected
`GITHUB_RUN_ATTEMPT=2`. Downstream audit/credentials were skipped, not accepted.
Both original complete failed logs remain separately retained. Neither failure
is reinterpreted as green or as a live database permission failure.

## Intentional current compatibility change

The current CI-only wrapper is v5. It obtains the canonical positive attempt
from the actual runner environment and binds that exact attempt together with
run ID, head, checkout and the verified full CI-plan binding in every packet.
All six packets must have one identity and one exact independently expected
bundle. Native producers derive a shared CI identity from that full bundle.
Every packet must be freshly generated in the observed attempt; earlier failed
attempt output cannot be substituted. Extractors must receive expected bundle
metadata from the trusted run context, not reflect it from packet headers.

Current APIs emit/accept v5 only. Separately named `historical_v4_*` functions
retain v4's distinct markers, format and strict attempt1 rule. There is no
automatic detection, negotiation or downgrade. Wire compatibility does not
re-admit old compiler/source receipts under current source authority.
Mixed versions, attempts, runs, profile identities or CI bindings fail closed.
Repository/event checks still reject manual dispatch. All existing complete
logical validators, peer hashes, canonical JSON/gzip and size/chunk limits stay
in force. No workflow, required step/job gate, timeout or coverage is changed.

The small test-only current-byte manifest records only intentionally modified
parent files. Its adapter validates full current bytes before returning exact
immutable base bytes for existing historical release assertions. No source
region is ignored and no unrelated repository bytes are newly frozen.

## Verification and boundary

The actual-attempt regression first failed the unchanged base at its original
`attempt == "1"` predicate. Unit tests cover current reruns, v4 byte-for-byte
wire compatibility, malformed attempts, CI binding and mixed-evidence rejection.
The three existing hosted PostgreSQL collectors keep their real database,
source, lock and full logical assertions; only two emission identities change.
Final-head local/full/native/image/audit/credential outcomes belong in this
PR's checks and description. Unit framing doubles are not native or live proof.

No production code, grants, policies, migrations, financial/source values,
dependencies, cloud jobs, IAM, secrets, schedules or deployments are changed.
RO-02 implementation still follows verified merged main. This extra repair PR
must not merge without authority beyond the original two named merges.
