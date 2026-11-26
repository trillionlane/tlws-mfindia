"""Explicit v5 -> v9 SBI dual-date transition; no ingestion or activation side effects."""

import hashlib
from pathlib import Path

from psycopg.types.json import Jsonb

from mfdataindia.enrichment import book_sources as legacy_sources
from mfdataindia.enrichment import current_facts_store as current
from mfdataindia.enrichment import sbi_book_sources_v2 as book_sources
from mfdataindia.enrichment import sbi_book_store_v2 as book_store
from mfdataindia.enrichment.monthly_projection import owner, seal_configuration
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint

SOURCE_VERSION = 5
TARGET_VERSION = 9
PROJECTION = "amc_book_current_projection_v4.sql.inc"
PROJECTION_SHA256 = "4861ce38094f26b07b3d40dcdc7b47d2a69218d8f607c47da5ba6a9c6b04eb80"


def projection_source(root):
    raw = (Path(root) / "sql/optional" / PROJECTION).read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROJECTION_SHA256:
        raise ValueError("SBI_DOCUMENT_DATE_PROJECTION_DIFFERS")
    return raw.decode()


def install(conn, root, *, expected_book_hash, expected_current_hash):
    """Install only the reviewed v5-derived authority in one owner transaction."""
    with conn.transaction():
        owner(conn)
        book_store.verify_acl(conn, principal="mfdata_verified_reader")
        current.verify_acl(conn)
        installed = book_store.installed_configuration(conn)
        prior = dict(current.configuration(conn))
        target = book_sources.configuration(TARGET_VERSION)
        target_policy = book_sources.current_policy(TARGET_VERSION)
        if installed["document"] == target:
            if prior["policy_hash"] != fingerprint(target_policy):
                raise ValueError("SBI_DOCUMENT_DATE_INSTALLATION_INCOMPLETE")
            return {"status": "ALREADY_INSTALLED", "source_rows_changed": 0}
        if (
            installed["document"] != legacy_sources.configuration(SOURCE_VERSION)
            or installed["configuration_hash"] != expected_book_hash
            or fingerprint(prior) != expected_current_hash
            or prior["policy_hash"] != fingerprint(legacy_sources.current_policy(SOURCE_VERSION))
            or not current.projection_state(conn)
        ):
            raise ValueError("SBI_DOCUMENT_DATE_V5_BASE_REQUIRED")

        before = book_store.capture(conn)
        body = {
            "format": "sbi-document-date-upgrade-v1",
            "mode": "sbi_document_date_revision",
            "source_version": SOURCE_VERSION,
            "target_version": TARGET_VERSION,
            "prior_book_configuration": dict(installed),
            "prior_current_configuration_hash": fingerprint(prior),
            "target_configuration_hash": fingerprint(target),
            "source_rows_changed": 0,
        }
        intent = {**body, "transition_id": fingerprint(body)}
        book_store.append_exact(conn, "transitions", "transition_id", intent, {})

        raw = book_store.migration_source(root, version=TARGET_VERSION)
        if raw.count("\nBEGIN;\n") != 1 or not raw.rstrip().endswith("COMMIT;"):
            raise ValueError("SBI_DOCUMENT_DATE_MIGRATION_NOT_TRANSACTIONAL")
        conn.execute(raw.replace("\nBEGIN;\n", "\n", 1).rstrip()[: -len("COMMIT;")])
        conn.execute(
            "INSERT INTO mf_book_facts.configuration VALUES(%s,%s,%s)",
            (installed["generation"] + 1, fingerprint(target), Jsonb(target)),
        )
        review._insert_exact(
            conn,
            "policies",
            "policy_hash",
            fingerprint(target_policy),
            target_policy,
            {"policy_id": target_policy["id"], "policy_version": target_policy["version"]},
        )
        conn.execute(projection_source(root))
        definitions = dict(prior["view_definitions"])
        definitions["field_versions"] = current.view_definition(
            conn, "mf_live_facts.field_versions"
        )
        seal_configuration(
            conn,
            prior["generation"] + 1,
            prior["baseline_definition"],
            fingerprint(target_policy),
            definitions,
        )
        book_store.verify_acl(conn, principal="mfdata_verified_reader")
        current.verify_acl(conn)
        after = book_store.capture(conn)
        for key in ("sources", "batches", "runs"):
            if after[key] != before[key]:
                raise ValueError("SBI_DOCUMENT_DATE_HISTORY_CHANGED")
        if not before["configuration"].items() <= after["configuration"].items():
            raise ValueError("SBI_DOCUMENT_DATE_CONFIGURATION_HISTORY_CHANGED")
        return {
            "status": "INSTALLED",
            "transition_id": intent["transition_id"],
            "configuration_hash": fingerprint(target),
            "source_rows_changed": 0,
        }
