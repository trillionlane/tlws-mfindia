"""Exact bounded DEV monthly source imports, not unrestricted source fetches."""

import json
import re
from datetime import date
from pathlib import Path

import psycopg
import requests
from psycopg.rows import dict_row
from pydantic import Field, StrictInt

from mfdataindia.enrichment import monthly_schemas as schemas, monthly_store as store
from mfdataindia.enrichment.deployment import download, pinned_dsn
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import seal_evidence


class MonthlySource(schemas.Model):
    selection: schemas.Selection
    object_name: str
    generation: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    byte_length: StrictInt = Field(gt=0, le=16 * 1024 * 1024)

    @property
    def intake(self):
        return self.selection.intake


class MonthlyBatch(schemas.Model):
    sources: list[MonthlySource] = Field(min_length=1, max_length=2)

    def bind(self, manifest):
        if set(manifest.cohort) != {103173, 103174}:
            raise ValueError("monthly rollout exact two-variant cohort required")
        hashes = []
        for source in self.sources:
            checksum = source.intake.expected_sha256
            if (
                source.object_name
                != "monthly-facts-rollouts/" + manifest.rollout_id + "/" + checksum + ".pdf"
            ):
                raise ValueError("monthly original must belong to the exact rollout and hash")
            hashes.append(checksum)
        if len(set(hashes)) != len(hashes):
            raise ValueError("duplicate source in bounded batch")
        return fingerprint({"manifest": manifest.seal, "batch": self.model_dump(mode="json")})


def batch(manifest, environment):
    result = MonthlyBatch.model_validate_json(environment["MONTHLY_FACTS_BATCH"])
    if result.bind(manifest) != environment["MONTHLY_FACTS_BATCH_HASH"]:
        raise ValueError("monthly original-source approval binding differs")
    return result


def execute(manifest, environment, operation, *, root=Path("/app")):
    if operation not in ("install", "append", "inspect"):
        raise ValueError("unsupported monthly operation")
    role = "mfdata_verified_generator" if operation == "append" else "mfdata_admin"
    dsn = pinned_dsn(manifest, environment, role)
    if operation == "append":
        selected = batch(manifest, environment)
        with requests.Session() as session:
            originals = [download(source, session) for source in selected.sources]
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("SET LOCAL statement_timeout='120s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            conn.execute("SET LOCAL transaction_timeout='240s'")
            snapshots = []
            for source, raw in zip(selected.sources, originals, strict=True):
                snapshots.extend(store.publish(conn, source.selection, raw))
            result = {
                "snapshot_ids": [s["snapshot_id"] for s in snapshots],
                "sources": len(originals),
                "snapshots": len(snapshots),
                "source_rows_changed": 0,
                "original_records_replaced": False,
                "canonical_analytics_replaced": False,
                "verification_status": "needs_verification",
            }
    else:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                + (", READ ONLY" if operation == "inspect" else "")
            )
            conn.execute("SET LOCAL statement_timeout='120s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            result = (
                store.install(conn, root)
                if operation == "install"
                else {"inventory": store.capture(conn), "source_rows_changed": 0, "read_only": True}
            )
    return seal_evidence(
        manifest, "monthly-facts-" + operation, {"level": "DEV_DATABASE", **result}
    )


def references(manifest, environment):
    body = json.loads(environment["MONTHLY_FACTS_REFERENCE"])
    expected = fingerprint({"manifest": manifest.seal, "references": body})
    if (
        expected != environment["MONTHLY_FACTS_REFERENCE_HASH"]
        or not isinstance(body, list)
        or len(body) != 4
    ):
        raise ValueError("bounded independent monthly references required")
    seen = set()
    for row in body:
        if (
            set(row) != {"code", "snapshot_id", "as_of", "fields"}
            or row["code"] not in manifest.cohort
            or not re.fullmatch("[0-9a-f]{64}", row["snapshot_id"])
            or date.fromisoformat(row["as_of"]).isoformat() != row["as_of"]
            or not isinstance(row["fields"], dict)
        ):
            raise ValueError("monthly source-reference shape differs")
        if (row["code"], row["snapshot_id"]) in seen:
            raise ValueError("duplicate monthly reference")
        seen.add((row["code"], row["snapshot_id"]))
        if not {
            "risk_level",
            "benchmark_name",
            "fund_managers",
            "base_expense_ratio",
            "aum",
        } <= set(row["fields"]):
            raise ValueError("risk, facts, BER and explicit AUM availability reference required")
        if any(
            not isinstance(value, dict) or set(value) != {"value", "unit", "status"}
            for value in row["fields"].values()
        ):
            raise ValueError("reference must bind value, explicit unit and availability status")
    if {r["code"] for r in body} != set(manifest.cohort):
        raise ValueError("both exact monthly variants must be referenced")
    periods = [{r["as_of"] for r in body if r["code"] == code} for code in manifest.cohort]
    if any(len(p) != 2 for p in periods) or periods[0] != periods[1]:
        raise ValueError("both actual reporting periods required for each variant")
    return body


def projections(conn, reviewed):
    from mfdataindia.enrichment import monthly_facts as engine

    cases = [
        (
            "/api/enrichment/v4/policy",
            {"policy_hash": fingerprint(engine.policy()), "document": engine.policy()},
        )
    ]
    for expected in reviewed:
        doc = store.get(conn, expected["code"], snapshot_id=expected["snapshot_id"])
        if doc["state"] != "available" or doc["snapshot"]["disclosure_as_of"] != expected["as_of"]:
            raise ValueError("monthly original-source snapshot unavailable")
        for name, value in expected["fields"].items():
            if {key: doc["snapshot"]["fields"][name][key] for key in value} != value:
                raise ValueError("monthly extraction differs from independently inspected original")
        cases.append(
            (
                f"/api/enrichment/v4/funds/{expected['code']}/facts?snapshot_id={expected['snapshot_id']}",
                doc,
            )
        )
    return cases
