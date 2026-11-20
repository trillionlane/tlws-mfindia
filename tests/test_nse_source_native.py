"""Synthetic source-only data, no NSE requests or shared database operations."""

from copy import deepcopy
from hashlib import sha256

import pytest
from pydantic import ValidationError

from mfdataindia.api.schemas import Sch44Fields
from mfdataindia.nse import contract as c, sch44_reconcile as identity, source_native as native
from mfdataindia.nse.schema import HEADERS44
from test_nse_sch44_reconcile import source, catalogue, amfi


def inputs(flag="N", slot=identity.REINVEST_SLOT):
    raw = source(flag=flag)
    provenance = {
        "channel": "NSE_PRODUCTION_WEBSITE",
        "filename": "NSE_NSEINVEST_ALL_17112026.txt",
        "retrieved_at_utc": "2026-11-17T13:03:30.761Z",
        "object_uri": "gs://synthetic-private/source.txt",
        "object_generation": "123",
        "sha256": sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "row_count": 1,
    }
    products = {
        "source_sha256": provenance["sha256"],
        "rows": [
            {
                "source_line": 2,
                "disposition": "MF_EXACT",
                "reason": "CONVENTIONAL_AMFI_EVIDENCE",
                "evidence_sha256": "1" * 64,
            }
        ],
    }
    return dict(
        raw=raw,
        provenance=provenance,
        catalogue=catalogue(slot),
        earlier_source=source(marker="Earlier"),
        amfi_first=amfi(slot),
        amfi_second=amfi(slot, date="16-Nov-2026"),
        product_review=products,
        product_review_sha256=c.digest(products),
    )


@pytest.mark.parametrize(
    ("flag", "slot"), [("N", identity.REINVEST_SLOT), ("Y", identity.GROWTH_SLOT)]
)
def test_all_44_fields_and_disagreement_are_separate(flag, slot):
    args = inputs(flag, slot)
    plan = native.prepare(**args)
    (row,) = plan["rows"]
    assert row["disposition"] == "MF_EXACT"
    assert row["cells"][27] == flag
    assert row["link"]["amfi_isin_slot"] == slot
    assert row["link"]["flag_slot_disagreement"] == identity.DISAGREEMENT
    fields = dict(zip(HEADERS44, row["cells"]))
    assert Sch44Fields(**fields).model_dump(by_alias=True) == fields
    assert fields["MATURITY DATE"] == "00-00-0000" and fields["EXIT LOAD"] == ""
    assert fields["NEW PURCHASE MIN AMOUNT"] == ".25"
    assert plan["metadata"]["generation_date"] == "2026-11-17"
    assert plan["metadata"]["effective_business_date"] is None
    with pytest.raises(ValidationError):
        Sch44Fields(**{**fields, "EXIT LOAD": None})  # blank is not JSON null


@pytest.mark.parametrize("disposition", ["SIF", "OTHER_VERIFIED_OUT_OF_SCOPE", "UNCLASSIFIED"])
def test_product_holds_cannot_create_canonical_links(disposition):
    args = inputs()
    args["product_review"]["rows"][0]["disposition"] = disposition
    args["product_review_sha256"] = c.digest(args["product_review"])
    plan = native.prepare(**args)
    assert plan["rows"][0]["disposition"] == disposition
    assert plan["rows"][0]["link"] is None
    assert plan["rows"][0]["reason"] == "CONVENTIONAL_AMFI_EVIDENCE"


@pytest.mark.parametrize("outcome", ["MF_EXACT", "MF_HELD", "MF_UNMATCHED"])
def test_identity_reason_follows_current_disposition_not_stale_product_review(outcome):
    args = inputs()
    args["product_review"]["rows"][0].update(
        disposition="MF_UNMATCHED", reason="STALE_PRODUCT_REASON"
    )
    args["product_review_sha256"] = c.digest(args["product_review"])
    if outcome == "MF_HELD":
        args["amfi_second"] = args["amfi_second"].replace(b"Regular Plan;", b";")
    elif outcome == "MF_UNMATCHED":
        args["raw"] = args["raw"].replace(b"INZZ00000019", b"INZZ00000027")
        args["provenance"]["sha256"] = sha256(args["raw"]).hexdigest()
        args["product_review"]["source_sha256"] = args["provenance"]["sha256"]
        args["product_review_sha256"] = c.digest(args["product_review"])
    current = identity.report(
        *(args[k] for k in ("raw", "catalogue", "earlier_source", "amfi_first", "amfi_second"))
    )["rows"][0]
    assert current["reason"] != "STALE_PRODUCT_REASON"
    (row,) = native.prepare(**args)["rows"]
    assert row["disposition"] == outcome
    assert row["reason"] == current["reason"]


def test_product_review_cannot_promote_unverified_identity():
    args = inputs()
    args["amfi_second"] = args["amfi_second"].replace(b"Regular Plan;", b";")
    (row,) = native.prepare(**args)["rows"]
    assert row["disposition"] == "MF_HELD" and row["link"] is None


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("channel", "UAT", "PRODUCTION_SOURCE_REQUIRED"),
        ("object_generation", None, "SOURCE_ARCHIVE_GENERATION_REQUIRED"),
        ("sha256", "0" * 64, "SOURCE_BYTES_DIFFER"),
        ("row_count", True, "SOURCE_BYTES_DIFFER"),
        ("filename", "NSE_NSEINVEST_ALL_31112026.txt", "SOURCE_RETRIEVAL_INVALID"),
        ("retrieved_at_utc", "2026-11-17T13:03:30", "SOURCE_RETRIEVAL_INVALID"),
    ],
)
def test_source_pins_before_any_database_call(field, value, reason):
    args = inputs()
    args["provenance"][field] = value
    with pytest.raises(c.ImportError, match=reason):
        native.prepare(**args)


def test_independent_product_pin_and_clean_truncation():
    args = inputs()
    args["product_review"]["rows"][0]["disposition"] = "SIF"
    with pytest.raises(c.ImportError, match="PRODUCT_REVIEW_UNTRUSTED"):
        native.prepare(**args)
    args = inputs()
    args["provenance"]["row_count"] = 2
    with pytest.raises(c.ImportError, match="SOURCE_BYTES_DIFFER"):
        native.prepare(**args)
    # Publication accepts only a separately reviewed complete candidate hash.
    plan = native.prepare(**inputs())
    changed = deepcopy(plan)
    changed["rows"][0]["link"]["amfi_code"] = 999999
    with pytest.raises(c.ImportError, match="SOURCE_CANDIDATE_UNREVIEWED"):
        native.publish(
            None,
            inputs()["raw"],
            changed,
            c.digest(plan),
            "11111111-1111-4111-8111-111111111111",
            expected_head=None,
        )


@pytest.mark.parametrize("row_count", [1, 1800])
def test_actual_file_preparation_cli_is_private_and_offline(tmp_path, capsys, row_count):
    import runpy
    from pathlib import Path

    main = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/prepare_nse_source_native.py")
    )["main"]
    args = inputs()
    if row_count > 1:
        header, row = args["raw"].splitlines()
        args["raw"] = b"\n".join([header] + [row] * row_count) + b"\n"
        args["provenance"].update(
            sha256=sha256(args["raw"]).hexdigest(),
            byte_length=len(args["raw"]),
            row_count=row_count,
        )
        decision = args["product_review"]["rows"][0]
        args["product_review"].update(
            source_sha256=args["provenance"]["sha256"],
            rows=[{**decision, "source_line": line} for line in range(2, row_count + 2)],
        )
        args["product_review_sha256"] = c.digest(args["product_review"])
        assert len(c.canonical(args["product_review"])) > 256 * 1024
    argv = []
    for key in (
        "raw",
        "provenance",
        "catalogue",
        "earlier_source",
        "amfi_first",
        "amfi_second",
        "product_review",
    ):
        target = tmp_path / key
        value = args[key]
        target.write_bytes(c.canonical(value) if isinstance(value, dict) else value)
        argv.extend(("--" + ("source" if key == "raw" else key.replace("_", "-")), str(target)))
    output = tmp_path / "candidate.json"
    argv.extend(("--product-review-sha256", args["product_review_sha256"], "--output", str(output)))
    assert main(argv) == 0
    assert output.read_bytes() == c.canonical(native.prepare(**args))
    assert capsys.readouterr().out.strip() == c.digest(native.prepare(**args))
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(SystemExit) as error:
        main(argv)
    assert error.value.code == 2


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("filename", None, "SOURCE_FILENAME_INVALID"),
        ("retrieved_at_utc", None, "SOURCE_RETRIEVAL_INVALID"),
    ],
)
def test_malformed_metadata_fails_with_allowlisted_reason(field, value, reason):
    args = inputs()
    args["provenance"][field] = value
    with pytest.raises(c.ImportError, match=reason):
        native.prepare(**args)


def test_candidate_hash_survives_json_file_roundtrip():
    import json

    plan = native.prepare(**inputs())
    raw = c.canonical(plan)
    decoded = json.loads(raw)
    assert c.canonical(decoded) == raw
    assert c.digest(decoded) == c.digest(plan) == sha256(raw).hexdigest()
    assert all(isinstance(k, str) for k in plan["rows"][0]["field_holds"])


def test_snapshot_identity_excludes_only_retrieval_observations():
    plan = native.prepare(**inputs())
    replay = deepcopy(plan)
    replay["metadata"].update(
        retrieved_at_utc="2026-11-19T10:00:00Z",
        object_generation="456",
        object_uri="gs://synthetic-private/replay.txt",
    )
    assert c.digest(replay) != c.digest(plan)  # still separately reviewed/bound
    assert native.snapshot_key(replay) == native.snapshot_key(plan)
    replay["rows"][0]["disposition"] = "MF_HELD"
    assert native.snapshot_key(replay) != native.snapshot_key(plan)


def test_source_storage_composition_preserves_templates_and_limits_roles():
    from mfdataindia.nse import source_storage, source_roles
    from mfdataindia.datasets import catalogue

    ddl, digest = source_storage.definition()
    assert len(digest) == 64
    assert (
        sha256(source_storage.store.MIGRATION.read_bytes()).hexdigest()
        == source_storage.TEMPLATE45_SHA256
    )
    assert "CREATE SCHEMA" not in ddl and "CREATE ROLE" not in ddl
    assert "AUTHORIZATION mfdata_admin" not in ddl
    assert "TO mfdata_ingest" not in ddl
    assert "GRANT SELECT, UPDATE ON mf_nse.family_heads" not in ddl
    assert "GRANT SELECT, INSERT, DELETE ON mf_nse.current_sch" not in ddl
    assert "GRANT SELECT ON mf.amcs" not in ddl
    assert "TO " + source_roles.WRITER in ddl
    assert catalogue.creator_supported("mf_nse", source_roles.OWNER)
    assert not catalogue.creator_supported("mf_unrelated", source_roles.OWNER)
    assert not catalogue.creator_supported("mf", source_roles.OWNER)


def test_source_native_cases_use_existing_exhaustive_early_partition():
    from test_ci_test_partition import Partition, Item, collect

    items = [
        Item("tests/test_nse_source_native_postgres.py::test_roles", True),
        Item("tests/test_uti_v2_store.py::test_retained", True),
        Item("tests/test_nse_source_native.py::test_file_only"),
    ]
    first, dropped = collect(Partition("focused"), items)
    assert first == [items[0].nodeid, items[1].nodeid]
    assert dropped == [items[2].nodeid]


@pytest.mark.parametrize("change", ["legacy_rule", "missing_rule", "wrong_witness_hash"])
def test_publication_rejects_stale_identity_contract_before_database(change):
    args = inputs()
    plan = native.prepare(**args)
    if change == "legacy_rule":
        plan["metadata"]["identity_rule"] = "nse-sch44-production-link-review-v1"
    elif change == "missing_rule":
        plan["metadata"].pop("identity_rule")
    else:
        plan["metadata"]["identity_evidence"]["source"] = "0" * 64
    expected = (
        "SOURCE_IDENTITY_EVIDENCE_DIFFERS"
        if change == "wrong_witness_hash"
        else "SOURCE_IDENTITY_RULE_DIFFERS"
    )
    with pytest.raises(c.ImportError, match=expected):
        native.publish(
            None,
            args["raw"],
            plan,
            c.digest(plan),
            "11111111-1111-4111-8111-111111111111",
            expected_head=None,
        )
