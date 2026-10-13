"""Archive supplied authoritative bytes and stage exact NAV differences.

No network fetch and no source-table writes. URL provenance is an operator
attestation, not authentication of supplied bytes; conclusive decisions require
review of the archived original. AMC documents can support identity/event
review but cannot supply replacement NAVs under the AMFI-only source policy.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from mfdataindia.audit.remediation import canonical, require_hash, seal, verify
from mfdataindia.ingest.amfi_nav_history import parse_nav_history_report


def nav_decimal(value) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ValueError("NAV must be an exact decimal, never a float")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError("invalid decimal NAV") from None
    if (
        not number.is_finite()
        or number < 0
        or number >= Decimal("100000000000000")
        or number != number.quantize(Decimal("0.0001"))
    ):
        raise ValueError("NAV must fit NUMERIC(18,4) without rounding")
    return number.quantize(Decimal("0.0001"))


def validate_evidence_reference(reference: dict) -> None:
    verify(reference)
    if reference.get("format") != "mfdata-nav-evidence-v1":
        raise ValueError("unsupported evidence format")
    require_hash(reference.get("raw_sha256"))
    if (
        type(reference.get("content_bytes")) is not int
        or not 0 < reference["content_bytes"] <= 32 * 1024 * 1024
    ):
        raise ValueError("invalid evidence byte count")
    url = urlsplit(reference["url"])
    if (
        url.scheme != "https"
        or url.username
        or url.password
        or url.fragment
        or url.port not in (None, 443)
        or not url.hostname
    ):
        raise ValueError("evidence requires an uncredentialed HTTPS source URL")
    when = datetime.fromisoformat(reference["fetched_at"].replace("Z", "+00:00"))
    if when.tzinfo is None or when > datetime.now(timezone.utc):
        raise ValueError("evidence timestamp must be timezone-aware and not future-dated")
    if reference["source"] == "AMFI_NAV_HISTORY":
        if (
            url.hostname not in {"portal.amfiindia.com", "www.amfiindia.com"}
            or url.path != "/DownloadNAVHistoryReport_Po.aspx"
        ):
            raise ValueError("replacement NAV evidence must identify AMFI history")
    elif reference["source"] == "AMC_OFFICIAL_DOCUMENT":
        if (
            not reference.get("official_host")
            or url.hostname != reference["official_host"]
            or not reference.get("issuer_reference")
        ):
            raise ValueError(
                "AMC evidence needs an independently reviewed official host and issuer"
            )
    else:
        raise ValueError("unsupported evidence source")
    if reference["http_status"] != 200:
        raise ValueError("source acquisition must have succeeded")


def archive_evidence(
    raw_path: Path,
    directory: Path,
    *,
    source: str,
    url: str,
    fetched_at: str,
    official_host: str | None = None,
    issuer_reference: str | None = None,
) -> dict:
    raw = raw_path.read_bytes()
    if not raw or len(raw) > 32 * 1024 * 1024:
        raise ValueError("evidence must contain between 1 byte and 32 MiB")
    raw_hash = hashlib.sha256(raw).hexdigest()
    reference = seal(
        {
            "format": "mfdata-nav-evidence-v1",
            "source": source,
            "url": url,
            "fetched_at": fetched_at,
            "http_status": 200,
            "raw_sha256": raw_hash,
            "content_bytes": len(raw),
            "official_host": official_host,
            "issuer_reference": issuer_reference,
            "acquisition": "OPERATOR_SUPPLIED_ORIGINAL",
        }
    )
    validate_evidence_reference(reference)
    directory.mkdir(parents=True, exist_ok=True)
    write_once(directory / f"{raw_hash}.bin", raw)
    write_once(directory / f"{reference['sha256']}.json", (canonical(reference) + "\n").encode())
    return reference


def write_once(path: Path, content: bytes) -> None:
    """Refuse clobbering an existing immutable artifact, including a symlink."""
    if path.is_symlink():
        raise ValueError("artifact path is a symlink")
    try:
        with path.open("xb") as target:
            target.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise ValueError("immutable artifact already exists with different content") from None


def load_amfi_observations(reference: dict, archive: Path) -> dict[tuple[int, date], Decimal]:
    validate_evidence_reference(reference)
    if reference["source"] != "AMFI_NAV_HISTORY":
        raise ValueError("AMC documents cannot authorize replacement NAV values")
    raw_path = archive / f"{reference['raw_sha256']}.bin"
    raw = raw_path.read_bytes()
    if (
        hashlib.sha256(raw).hexdigest() != reference["raw_sha256"]
        or len(raw) != reference["content_bytes"]
    ):
        raise ValueError("archived source bytes were changed")
    text = raw.decode("utf-8-sig")
    raw_lines = text.splitlines()
    rows, report = parse_nav_history_report(text)
    if (
        report.header_rows != 1
        or report.quarantined
        or report.unparsed_nav
        or report.unparsed_date
        or not rows
    ):
        raise ValueError("AMFI evidence has malformed or incomplete parse results")
    observations = {}
    for row in rows:
        if row.nav is None or row.nav_date is None:
            continue  # Published N.A. is not a replacement NAV.
        key = (int(row.amfi_scheme_code), row.nav_date)
        # The shared parser uses float for its legacy ingest representation.
        # Remediation must preserve the published decimal bytes without that
        # conversion (or a rounding operation) influencing a replacement.
        nav_text = raw_lines[row.line_no - 1].split(";")[6].strip().replace(",", "")
        value = nav_decimal(nav_text)
        if key in observations and observations[key] != value:
            raise ValueError("source contains conflicting values for one scheme/date")
        observations[key] = value
    if not observations:
        raise ValueError("source contains no usable NAV observations")
    return observations


def build_proposal(
    conn,
    *,
    case: dict,
    decision: dict,
    reference: dict,
    archive: Path,
    expected_dataset_version: int,
) -> dict:
    """Existing-key updates only; missing history is reported, never inserted.

    This function requires a caller-owned locked, read-only transaction. It
    produces a proposal artifact, not an executable production correction.
    """
    verify(case)
    verify(decision)
    if (
        decision["case_snapshot_sha256"] != case["sha256"]
        or decision["disposition"] != "confirmed_nav_discrepancy"
    ):
        raise ValueError("proposal requires a discrepancy decision for this exact snapshot")
    if reference not in decision["evidence"]:
        raise ValueError("replacement evidence is not bound to the reviewed decision")
    observations = load_amfi_observations(reference, archive)
    manifest = conn.execute(
        "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
    ).fetchone()
    if manifest is None or manifest["dataset_version"] != expected_dataset_version:
        raise ValueError("dataset changed before proposal construction")
    if case["dataset_version"] != expected_dataset_version:
        raise ValueError("case snapshot is stale; regenerate and review it")
    revisions = case.get("reviewed_source_revisions")
    if (not isinstance(revisions, dict) or set(revisions) != {str(c) for c in case["scheme_codes"]}
            or any(type(v) is not int or v < 0 for v in revisions.values())):
        raise ValueError("case has no exact reviewed source dependencies; export and review a fresh case")
    current = {str(r['amfi_scheme_code']): r['revision'] for r in conn.execute(
        "SELECT * FROM mf.nav_source_revisions WHERE amfi_scheme_code=ANY(%s)", (case['scheme_codes'],)
    ).fetchall()}
    if current != revisions:
        raise ValueError("reviewed source dependencies changed before proposal construction")
    rows = conn.execute(
        "SELECT * FROM mf.nav_history WHERE amfi_scheme_code = ANY(%s) ORDER BY amfi_scheme_code,nav_date",
        (case["scheme_codes"],),
    ).fetchall()
    before = {(int(r["amfi_scheme_code"]), r["nav_date"]): dict(r) for r in rows}
    changes, missing, covered = [], [], 0
    for key, proposed in sorted(observations.items()):
        if key[0] not in case["scheme_codes"]:
            continue
        original = before.get(key)
        if original is None:
            missing.append({"amfi_scheme_code": key[0], "nav_date": key[1].isoformat()})
            continue
        covered += 1
        if nav_decimal(original["nav"]) != proposed:
            changes.append(
                {
                    "amfi_scheme_code": key[0],
                    "nav_date": key[1].isoformat(),
                    "before": json.loads(canonical(original)),
                    "after_nav": format(proposed, ".4f"),
                }
            )
    if not covered:
        raise ValueError("evidence has no overlapping observations for this case")
    if not changes:
        raise ValueError("evidence establishes no NAV discrepancy")
    if len(changes) > 100 or len({r["amfi_scheme_code"] for r in changes}) > 5:
        raise ValueError("proposal exceeds the first-pilot limit of 5 schemes / 100 NAV rows")
    return seal(
        {
            "format": "mfdata-nav-correction-proposal-v1",
            "case_snapshot_sha256": case["sha256"],
            "decision_sha256": decision["sha256"],
            "expected_dataset_version": expected_dataset_version,
            "reviewed_source_revisions": revisions,
            "evidence": reference,
            "changes": changes,
            "matched_observations": covered,
            "missing_stored_observations": missing,
            "status": "PROPOSED_NOT_APPROVED_OR_APPLIED",
        }
    )
