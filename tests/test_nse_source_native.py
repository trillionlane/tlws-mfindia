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


def test_actual_file_preparation_cli_is_private_and_offline(tmp_path, capsys):
    import runpy
    from pathlib import Path

    main = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/prepare_nse_source_native.py")
    )["main"]
    args = inputs()
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
