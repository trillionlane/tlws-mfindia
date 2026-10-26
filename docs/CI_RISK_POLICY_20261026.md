# CI coverage policy

Every PR produces the existing required checks. Neither a failed coverage plan
nor an unexpected skipped test can turn them green.

| Change | PR coverage |
| --- | --- |
| `README.md` and new, unfrozen prose in `docs/` only | Prose, diff and credential checks; no PostgreSQL or image builds |
| Only the nine existing MFData web assets, optionally with prose | All unit tests, all contracts, lint/audit/credentials, the complete core API PostgreSQL integration module, and both image builds/smokes |
| Backend/API, SQL, permissions, financial calculations, ingestion, recovery, CI, dependencies, tests, new/unknown paths, or mixed higher-risk changes | Complete original PostgreSQL test inventory and both image jobs |
| Every push to `main` and manual Verify run | Complete regression |

The web allowlist is deliberately finite. Backend/API changes stay full until
their dependency maps have been reviewed; there is no blanket API exemption.
Renames include both paths. Symlinks, executable changes, malformed event data,
missing Git history, failed planning and mismatched checkout evidence cannot
select a weaker route.

The user-approved standing whole-file stasis exception covers exactly README
and the nine existing web assets. Historical source-stasis checks restore their
frozen bytes only after attesting the actual regular Git HEAD and disk bytes.
Functional tests and image builds still read the actual changed files.
Historical documentation, evidence, runbooks and security/data policies are
not exempt; edits there remain full/per-change disposition. Deletions and
type changes also require full checks. No data, SQL, API or security exception
was added, and new web filenames do not inherit the exception.

The full PostgreSQL job no longer has our explicit 30-minute timeout. GitHub's
default/platform timeout still applies (currently 360 minutes); this is not an
unlimited job. The separate image job retains its 30-minute limit.

The focused 13 native tests and exact remaining complement are unchanged in
full mode. No fixture data, SQL, assertions, native tests or recovery scenarios
were removed. Targeted coverage is labelled partial and cannot satisfy the
full-native admission command. Full `main` regression remains a prerequisite
to the existing release process; this policy does not deploy or activate data
access.

The policy is covered by classification, temporary-Git, workflow, step-outcome,
collection, exit-code and complete historical-byte preservation tests. It does
not claim immunity against administrators or a malicious future workflow.
