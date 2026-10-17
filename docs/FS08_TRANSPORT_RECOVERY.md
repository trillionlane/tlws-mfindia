# FS08 transport recovery before DEV acceptance

## Observed DEV evidence, not a rollout acceptance

The fresh actual-owner restored clone at SHA `4ad64884` passed full original
preservation, both ABSL editions, least-privilege HTTP reads and recovery/replay.
The DEV book schema was installed additively, but **no new review service or
current-book overlay was activated**. The source gate failed closed and restored
the accepted FS07 service and two-task source-job configuration. Retained source
evidence and failed attempts were not deleted. FS08 remains unaccepted until a
fresh controlled rollout and genuine scheduled execution pass all gates.

Two actual operational defects were isolated:

1. `mf-book-04-before-dev-absl-261017b-hhfvk` completed, but the 971,259-byte
   inspection certificate was truncated to 102,400 bytes in stdout. Its partial
   log was not accepted. A new read-only capture used lossless compression,
   retained the full inner seal and independently checked its byte count/hash.
2. The ordinary FS07 scheduled execution `tlws-mf-official-facts-refresh-dev-5xxlq`
   failed HDFC acquisition before the new source runtime was installed. The FS08
   source execution also failed HDFC, so it was not certified. Read-only
   diagnostics, execution `mf-fs08b-hdfc-address-family-261017b-mqghv`, found public IPv6 answers sorted before
   the available public IPv4 answers. The chosen IPv6 socket failed with
   `OSError`, errno101, on the established IPv4-only DEV egress path.

The local operational ledger is `artifacts/mf-fs08b-dev-20261017-lFMJwI/`.
The original rows, accepted facts, backups, failed attempts and restricted
identities remain preserved. NAV ingestion/audit schedules were not paused.

## Narrow corrections

The shared downloader checks **every** DNS answer, then prefers numeric IPv4
before numeric IPv6. It still makes one connection to one approved numeric
address, preserving original-host SNI and certificate verification. There is no
address rotation, retry, proxy, cookie, credential, challenge bypass or source
URL/policy change. IPv6-only public sources remain supported outside IPv4-only
paths, with the same checks and failure behavior.

The AMC book owner CLI renders one bounded JSON certificate line. Small
certificates retain the legacy format; large certificates use the versioned
`mfdata-review-evidence-transport-v1` zlib/base64 envelope. Raw size is bounded
at 8 MiB and the rendered line at 80,000 bytes, below the observed log-entry limit.
Incompressible/oversized output fails instead of emitting a partial certificate.
The cloud verifier recognizes both forms, rejects malformed envelopes, bounds
decompression, checks full length/hash and rejects truncation, trailing streams,
duplicate inner keys and zero/multiple certificates. **This is transport only:**
the unchanged manifest/stage/inner evidence seal must still pass the original
verification. Runtime identity, image, network, secret and completion checks all
remain required before logs are examined.

No original NAV/fund row, old source registry, deterministic derivation policy,
accepted parser, raw source byte, analytics, UI or production configuration is
changed. This merge is not proof that the corrected image is serving or that a
scheduled source execution succeeded; record those only after renewed DEV gates.
