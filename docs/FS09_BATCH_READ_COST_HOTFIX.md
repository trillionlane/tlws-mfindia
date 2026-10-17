# FS-09 bounded batch facts reads

The final-image restored-clone gate exposed a second, separate query-plan
problem after the scalar facade optimization: `funds_batch` joined the facts
facade to a mixed code/ISIN `OR` lookup. PostgreSQL evaluated all 14,385 fund
facts, then discarded unrelated rows. Actual isolated-image requests took
25–29 seconds, while detail reads took 0.17–0.30 seconds. This was not accepted
as an SBI rollout certificate.

Resolve metadata and latest NAV first, then query the same current-facts facade
with an explicit array of only the matched scheme codes. Both statements remain
inside the existing repeatable-read, read-only API transaction. There is one
bounded facts query, not a request-per-fund loop. Zero matches skip facts work.

Response shape, identifier order/deduplication, raw-benchmark fallback,
same-plan/option/periodicity sibling rules, per-code performance isolation,
quality/lifecycle/methodology policy and `no-store` behavior are unchanged.
No data, source policy, views, schemas, roles, grants, runtime flags or UI are
changed by this hotfix. Its API image must be privately certified and explicitly
promoted with prior-traffic rollback before resuming source activation.

Validation requires scope-regression unit cases, PostgreSQL batch/fallback and
layered-facts integration cases, whole CI, and final-image actual restored-clone
latency/correctness checks. Merge alone does not establish DEV readiness.
