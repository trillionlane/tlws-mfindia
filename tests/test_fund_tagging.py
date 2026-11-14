"""Provider-free TAG-01 regressions, not per-fund or live acceptance."""

from copy import deepcopy
from datetime import date
import hashlib
from pathlib import Path

import pytest

from mfdataindia.load.fund_family import tags_for, tlws_mf_id
from mfdataindia.tagging import policy as p, store

ROOT = Path(__file__).resolve().parents[1]


def row(**changes):
    value = {
        "code": 1,
        "amc_id": 1,
        "amc": "Synthetic AMC",
        "scheme_name": "Example Growth",
        "scheme_type": "OPEN_ENDED",
        "scheme_category": "Equity Scheme - Large Cap Fund",
        "category_source": "AMFI",
        "plan_type": "REGULAR",
        "plan_source": "COLUMN",
        "option_type": "GROWTH",
        "is_etf": False,
        "in_scope": True,
        "last_seen": "2026-10-31",
        "group_key": "SYNTHETIC",
        "family_id": tlws_mf_id("SYNTHETIC"),
    }
    value.update(changes)
    return value


def assessment(document, dimension, code=1):
    return next(
        a for a in document["assessments"] if a["code"] == code and a["dimension"] == dimension
    )


def test_existing_array_is_replaceable_untyped_not_an_assessment_store():
    assert tags_for("X", "X", "x", None) == ["x"]
    assert (
        "tags             = EXCLUDED.tags"
        in (ROOT / "src/mfdataindia/load/fund_family.py").read_text()
    )
    candidate = p.prepare([row(amc="X")])
    assert assessment(candidate, "amc")["dimension"] == "amc"
    assert assessment(candidate, "amc")["evidence_hash"] == p.digest(row(amc="X"))


def test_complete_census_typed_facts_and_no_ethics_from_name():
    original = row(scheme_name="Synthetic Green Moral Sattvik Fund")
    candidate = p.prepare([original])
    assert len(candidate["assessments"]) == len(p.DIMENSIONS)
    assert candidate["funds"] == candidate["in_scope_funds"] == 1
    assert assessment(candidate, "asset-class")["value"] == "equity"
    assert assessment(candidate, "option")["value"] == "GROWTH"
    for dimension in ("environment", "values-screen", "esg-strategy", "risk", "strategy"):
        actual = assessment(candidate, dimension)
        assert actual["state"] == "not-assessed" and actual["value"] is None
    assert candidate["consumer_acceptance"] == "NOT_OBSERVED"
    assert original == row(scheme_name="Synthetic Green Moral Sattvik Fund")


@pytest.mark.parametrize(
    "change,dimension,reason",
    [
        ({"last_seen": None}, "amc", "SOURCE_OBSERVATION_DATE_MISSING"),
        ({"plan_source": "NAME"}, "plan", "EXPLICIT_PLAN_EVIDENCE_MISSING"),
        ({"category_source": "MANUAL"}, "scheme-category", "CATEGORY_AUTHORITY_NOT_AMFI"),
        ({"category_source": "MANUAL"}, "asset-class", "CATEGORY_AUTHORITY_NOT_AMFI"),
        ({"scheme_category": "Future category"}, "asset-class", "UNMAPPED_FACT_VALUE"),
        ({"option_type": "UNKNOWN"}, "option", "UNMAPPED_FACT_VALUE"),
    ],
)
def test_unknown_and_non_authoritative_inputs_are_not_positive(change, dimension, reason):
    result = assessment(p.prepare([row(**change)]), dimension)
    assert result["state"] == "insufficient-evidence" and result["value"] is None
    assert result["reason"] == reason


@pytest.mark.parametrize(
    "field,value",
    [
        ("amc_id", 2),
        ("scheme_category", "Debt Scheme - Liquid Fund"),
        ("scheme_type", "CLOSE_ENDED"),
    ],
)
def test_same_key_conflicting_membership_withheld_without_losing_scheme_facts(field, value):
    result = p.prepare([row(), row(code=2, **{field: value})])
    assert result["mapping_counts"] == {"FAMILY_AMBIGUOUS": 2}
    assert all(a["family_id"] is None for a in result["assessments"])
    assert assessment(result, "amc")["state"] == "supported"


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"group_key": None}, "FAMILY_MISSING"),
        ({"family_id": None}, "FAMILY_MISSING"),
        ({"family_id": "wrong"}, "FAMILY_AMBIGUOUS"),
    ],
)
def test_orphan_and_wrong_uuid_do_not_get_reissued(change, expected):
    actual = assessment(p.prepare([row(**change)]), "amc")
    assert actual["mapping_state"] == expected and actual["family_id"] is None


def test_plan_option_difference_and_out_of_scope_accounted():
    result = p.prepare(
        [row(), row(code=2, option_type="IDCW"), row(code=3, plan_type="DIRECT", in_scope=False)]
    )
    assert result["mapping_counts"] == {"FAMILY_VERIFIED": 3}
    assert assessment(result, "option", 2)["value"] == "IDCW"
    assert all(a["state"] == "not-applicable" for a in result["assessments"] if a["code"] == 3)
    assert sum(result["counts"].values()) == 3 * len(p.DIMENSIONS)


def test_stable_replay_and_revised_same_period_or_rename_are_distinct():
    a, b = row(), row(code=2)
    assert p.prepare([a, b]) == p.prepare([b, a])
    for change in ({"amc": "Revised AMC"}, {"scheme_name": "Renamed"}, {"last_seen": "2026-11-01"}):
        assert p.prepare([row(**change)])["snapshot_id"] != p.prepare([a])["snapshot_id"]
    assert p.prepare([row(amc="A&B")])["definitions"] != p.prepare([row(amc="A B")])["definitions"]


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [row(), row()],
        [row(code=True)],
        [row(code=0)],
        [row(last_seen="not-date")],
        [row(last_seen="2026-02-31")],
        [row(amc="secret\nmarker")],
        [row(unknown="must reject")],
        [row(in_scope=1)],
    ],
)
def test_closed_bounded_inputs_fail_value_free(bad):
    with pytest.raises(p.TaggingError) as caught:
        p.prepare(bad)
    assert str(caught.value).startswith("INVALID_")
    assert "secret" not in str(caught.value)


def test_dimensions_accounting_and_source_binding():
    assert len(p.DIMENSIONS) == len(set(p.DIMENSIONS))
    assert set(p.RESERVED).issubset(p.DIMENSIONS)
    assert hashlib.sha256((ROOT / store.FILE).read_bytes()).hexdigest() == store.SQL_SHA256
    assert "current_fund_facts" not in store.CENSUS_SQL  # No unproven legacy metric reuse.
    assert "LIMIT 50001" in store.CENSUS_SQL
    assert p.prepare([row()])["policy_hash"] == p.digest(
        {
            "contract": p.CONTRACT,
            "implementation_sha256": hashlib.sha256(Path(p.__file__).read_bytes()).hexdigest(),
        }
    )


def test_census_limit_is_before_any_publication(monkeypatch):
    monkeypatch.setattr(p, "MAX_FUNDS", 1)
    with pytest.raises(p.TaggingError, match="INVALID_FUND_CENSUS"):
        p.prepare([row(), row(code=2)])


def test_preparation_does_not_depend_on_current_clock():
    before = deepcopy(row())
    assert p.prepare([before]) == p.prepare([before])
    assert (
        date.fromisoformat(assessment(p.prepare([before]), "amc")["as_of"]).isoformat()
        == "2026-10-31"
    )


def test_real_main_is_offline_and_does_not_read_environment(tmp_path, capsys, monkeypatch):
    import json
    from mfdataindia.tagging.__main__ import main
    import psycopg

    monkeypatch.setenv("MFDATAINDIA_DSN", "synthetic-secret-marker")
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: pytest.fail("offline main connected"))
    path = tmp_path / "census.json"
    path.write_text(json.dumps([row()]))
    assert main(["--input", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "OFFLINE_VALIDATED_NOT_PUBLISHED"
    assert report["funds"] == 1 and "synthetic-secret-marker" not in str(report)
    path.write_text('[{"code":1,"code":2}]')
    assert main(["--input", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "DUPLICATE_CENSUS_MEMBER"
    path.write_text("synthetic-secret-marker")
    assert main(["--input", str(path)]) == 1
    assert "synthetic-secret-marker" not in capsys.readouterr().out


def test_same_head_cannot_hide_damaged_projection(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from psycopg.pq import TransactionStatus

    candidate = p.prepare([row()])

    class Result:
        def fetchone(self):
            return {"snapshot_id": candidate["snapshot_id"]}

        def fetchall(self):
            return []  # Previously published projection was damaged.

    class Connection:
        info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

        def transaction(self):
            return nullcontext()

        def execute(self, *args):
            return Result()

    for name in ("limits", "principal", "installed"):
        monkeypatch.setattr(store, name, lambda *args: None)
    monkeypatch.setattr(store, "census", lambda conn: candidate)
    monkeypatch.setattr(
        store.source_evidence,
        "latest",
        lambda conn: {
                "publication_id": "a" * 64,
                "publication_sequence": 1,
            "catalogue_census_hash": candidate["catalogue_census_hash"],
            "family_projection_hash": candidate["family_projection_hash"],
        },
    )
    with pytest.raises(p.TaggingError, match="TAGGING_PUBLICATION_INCOMPLETE"):
        store.publish(Connection())


def test_definitions_bind_explicit_version_and_namespaced_identity(monkeypatch):
    before = p.prepare([row(amc="REGULAR")])
    defs = {d["dimension"]: d for d in before["definitions"]}
    assert defs["amc"]["value"] == defs["plan"]["value"]
    assert defs["amc"]["tag_id"] != defs["plan"]["tag_id"]
    assert all(d["policy_version"] == p.VERSION for d in defs.values())
    monkeypatch.setattr(p, "VERSION", "synthetic-next-taxonomy-v2")
    after = p.prepare([row(amc="REGULAR")])
    assert {d["tag_id"] for d in before["definitions"]}.isdisjoint(
        {d["tag_id"] for d in after["definitions"]}
    )
