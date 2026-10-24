# Separate dormant dependency security correction

Written before dependency or compatibility code changes. Direct human Approved
the separate security-only update of filelock to 4.2.1 and pypdf to 6.21.2,
compatibility tests and independent review. No runtime or PDF-job changes.
The earlier OCI correction remains a separate local commit; nothing is pushed
until all required local gates pass. This design does not waive those gates.

## Exact patch artifacts and scope

Upgrade only these two distributions in uv.lock, with pypdf==6.21.2 in the
project declaration. filelock is an existing transitive development dependency;
do not add it as a runtime dependency or resolve unrelated upgrades.

PyPI JSON was read for the exact releases; neither is yanked:

| Distribution | Wheel SHA-256 | Source SHA-256 |
| --- | --- | --- |
| pypdf 6.21.2 | 1d44d1a3a200f25062adff0085b02040e4646431ac2ccac22e42679e91466084 | 0a3e1c5c886a1be03646c16a04827cc166c2a0575c971b26a4bf89686551c2d4 |
| filelock 4.2.1 | 7be2662e9e826b9bba28afc0f843a123280d799a2ea44bb5d35ccec67905ae44 | c4c937ccac2a813cee448e662a56366cfb374c0f8702e75f295c412cc1c5d5a3 |

Retain all previous advisories and security behavior, including JPEG-only DCT
decoding, no Pillow dependency, missing Contents rejection, and explicit empty
stream acceptance. No audit ignores, skips, xfails, unsafe dependency isolation,
installed-version monkeypatch, enlarged limits or assertion weakening.

## Additive compatibility authority, not a lock-only relabel

The direct approval explicitly included compatibility work, not a lock-only
bump. The separate dormant files below implement that approved behavior while
preserving old exact-version guards. They add no source entrypoint or active
capability. This is not approval to activate them or change any existing job.

The canonical frozen baseline is
`docs/PDF_SECURITY_AUTHORITY_86AE_20261024.json`, base
`86ae2a3fc17e274101cb74f644f910d564451682`, SHA-256
`9805d11f7d0863e278682583d1e60b4b16fcd9643752e52b6141679122e51173`.
It binds all 29 historical files, five configurations, five policies, both old
layout policies and the complete UTI-v4 policy/engine hashes. The older 4157
artifact is retained as history, not substituted for this 86ae baseline.

Freeze every historical decoder, worker, pdf_security.py contract, source
registry/configuration/policy/runtime/operational guard at its original path
and bytes. In particular layout-v2 and UTI-v4 continue naming 6.21.1 and must
reject extraction under 6.21.2. Do not import-alias replacements under old names.

Add new dormant pdf_security_v2.py (dependency contract format v2), layout-v3
and UTI-v5 modules and isolated workers. The new contract binds exact version,
wheel/source hashes and the three additional pypdf advisories. New layout policy
version 3 retains all existing source/page/text/stream/CPU/wall/memory limits.
New UTI policy version 5 retains the exact original financial/geometry/AUM rules,
names the new dependency and the retained v4 security-policy hash. Its engine
hash seals its new module/worker/layout/dependency bytes AND the unchanged full
v4 engine hash, transitively retaining every original policy/worker input.
The UTI worker calls the frozen v3 mechanics only after new exact dependency
admission. A whole decode's original single wall deadline is unchanged.

No new active registry, policy number, SQL migration or command is allocated.
All PDF operational entrypoints remain unconditionally denied before manifest,
configuration, DSN, DB, provider or writes. No existing PDF job/image can change
without separate actual ledger/all-consumer transition and rollout authority.

## Explicit test and synthetic smoke transition

Retarget tests/test_pdf_security_compatibility.py, only the versioned
missing-Contents case in tests/test_uti_book_pdf.py, and the provider-free
scripts/smoke_pdf_security_compatibility.py to the new distinctly named modules
and exact 6.21.2 contract, not aliases. Retain every previous rejection,
independent hash-admission, geometry/AUM/financial and historical-authority
assertion. Record exact previous test/smoke hashes. Collection ID changes caused
only by the explicitly renamed module parameter are documented one-for-one;
no test case is removed. Existing CI step bodies/order remain unchanged; the
same smoke command now validates the new dormant worker in BOTH images.

Add tests proving old modules/contract/policies remain byte-identical and old
patched engines reject the new installed runtime before worker spawn. New
admission independently rejects old extractor, dependency, engine, policy and
self-hash even when other values are resealed. Test both actual new isolated
workers and retained geometry/AUM forms; tamper transitive lineage bytes to
prove the new engine seals them. Verify lock/artifact hashes, exact installed
versions and no unrelated distribution changes. Test actual filelock lock/
timeout/release behavior without cloud/DB/source access. Exercise new pypdf
malformed-xref protections with small synthetic fixtures or bounded structural
guards; never run unbounded hostile payloads in the main agent process.

Exact advisory proofs are separate and mandatory, retaining the earlier JPEG
restriction (GHSA-5hx5-vpf9-5wcv) as well as all three new fixes:

- GHSA-6c7f-9265-g8fw / CVE-2026-115572: a synthetic valid in-memory PDF and
  repeated missing-object lookup must cache the missing result; structural AST
  verifies the resolved-object membership guard returns even for cached None,
  before xref/recovery work. No large input or timed performance claim.
- GHSA-jr96-gvgh-j3r4 / CVE-2026-115585: structural AST proves both the full
  stream read and recovery-cache construction occur only under the
  `recovery_cache is None` guard. This specifically covers the follow-up fix,
  not just malformed-input rejection.
- GHSA-gmh7-3g9r-mjh5 / CVE-2026-115580: a small synthetic xref declaration
  exceeding the actual stream-size/20 bound must raise LimitReachedError.
  An outer test-only deadline bounds the separate isolated-worker malformed
  document check without changing any production worker limit.

The new contract names all four GHSA identifiers. These proofs are based on
the maintainer patches in py-pdf/pypdf PRs 4239 and 4240. No vendored patch,
upstream test asset download, audit waiver or benchmark acceptance is used.
The additional retargeted missing-Contents test's previous full-file SHA is
`f03941d285b41fcba25f8b5b06efc3987ccbbe316ff43f744d9fd222c9158664`;
all its other test bodies stay exact and the same rejection assertion remains.

## Delivery gates

Design review before implementation; failing tests before correction. Focused
and full non-PG tests, all contracts/lint/credentials/diff/audit and complete
collection reconciliation before push. Named exact-head review after push,
resolve findings and re-review; whole original HEAD Verify proof, normal
exact-head merge and independent full MAIN proof remain required by the
established workflow. No green local test substitutes for native image/PG CI.

The consumed metadata diagnostic's approval/root/START/job/execution/failure
stay immutable. No re-run, deletion, synthesized result, retroactive acceptance,
new images/deployment, DB/IAM/source/schedule change or original-query retry.
All FS13/NSE/EXP preservation and authority gates remain unchanged.
