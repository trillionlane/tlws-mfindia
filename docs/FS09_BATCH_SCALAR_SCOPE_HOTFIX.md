# FS-09: scalar-bounded layered facts reads

The next exact-image restored-clone attempt remained unaccepted: detail reads
took 0.2–0.4 seconds, but batch reads took 15–21 seconds with the SBI graph
active. No live SBI installation or activation occurred. Both failed attempts
and backups remain retained; the first API image fix is separately certified
against the previously accepted graph.

The actual scratch-image SQL diagnostic separated identifier lookup (0.068s)
from the array-filtered facts statement (15.826s). On identical restored data,
scalar equality took 0.201s and the parameterized lateral form took 0.190s.
The complete selected facts rows were identical. A top-level `ANY(array)`
predicate did not constrain the LEFT JOINed overlay as scalar equality did.

Use one SQL statement over the distinct resolved codes, with one scalar
predicate per code inside a lateral subquery. `OFFSET 0` prevents flattening
back into a global facts join; it changes no rows or field values. Apply the
same execution boundary to batch sibling eligibility and family-safe facts
fetches, retaining every existing AMC/plan/option/periodicity restriction and
canonical-source ordering. Resolve inputs before fetching facts so unused
historical ISIN matches do not enlarge the cohort. There is no per-code Python
query loop, new cache, view/table/policy change, or wider timeout.

Preserve the current RR/read-only API transaction, response contracts, order,
deduplication, raw-benchmark fallback, NULL/zero handling, and per-code
performance isolation. No source data, IAM, secrets, schedules, UI or PROD
changes are included. Require normal review/full CI/merge, an exact-image
private API rollout, and a new complete restored-clone/source/recovery/timer
chain before calling the expanded SBI graph accepted.
