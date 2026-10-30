"""Provider-free equality gates; no live PostgreSQL or permission grant."""

from copy import deepcopy

import pytest

from mfdataindia.review import broad_reader_live_snapshot as snapshot
from mfdataindia.review import broad_reader_policy as policy


def _bundle():
    before = {"relations": [{"oid": 4}], "functions": [{"oid": 6}], "defaults": []}
    after = {"relations": [{"oid": 4, "acl": "SELECT"}],
             "functions": [{"oid": 6}], "defaults": []}
    base = {"format": "mf-broad-reader-live-snapshot-v1-nonexecutable",
            "state": before, "candidates": [{"oid": 4}],
            "system_leaves": [{"oid": 8}],
            "function_rows": [
                {"oid": 6, "reader_schema_usage": False, "acl": "unchanged"},
                {"oid": 7, "reader_schema_usage": False, "acl": "unchanged"}],
            "ledger": [{"migration_name": "only"}],
            "migration_proof": {"rows": 44},
            "view_profile": {"profile": "BASE44_EXTENDED"},
            "plan": {"after": after, "full_after_sha256": policy.fingerprint(after),
                     "delta": {"relations": [4]}},
            "grant_authorized": False, "permission_enabled": False}
    return base, after


def test_complete_pregrant_reread_requires_whole_equality():
    before, _ = _bundle()
    assert snapshot.compare(before, deepcopy(before))["grant_authorized"] is False
    drift = deepcopy(before)
    drift["ledger"].append({"migration_name": "extra"})
    with pytest.raises(snapshot.SnapshotError):
        snapshot.compare(before, drift)


def test_complete_postgrant_only_exact_policy_delta_and_schema_usage():
    before, after_state = _bundle()
    after = deepcopy(before)
    after["state"] = after_state
    after["function_rows"][0]["reader_schema_usage"] = True
    after["plan"] = {"after": after_state, "delta": {"relations": []}}
    assert snapshot.compare_post(before, after)["permission_enabled"] is False
    for key, changed in (("candidates", [{"oid": 999}]),
                         ("ledger", [{"migration_name": "different"}])):
        drift = deepcopy(after)
        drift[key] = changed
        with pytest.raises(snapshot.SnapshotError):
            snapshot.compare_post(before, drift)
    drift = deepcopy(after)
    drift["function_rows"][0]["acl"] = "new-execute"
    with pytest.raises(snapshot.SnapshotError):
        snapshot.compare_post(before, drift)


def test_server_allocated_default_acl_row_oid_is_sole_normalization():
    before, after_state = _bundle()
    before_row = {"oid": 777, "defaclrole": 9, "defaclnamespace": 10,
                  "defaclobjtype": "r", "acl": []}
    after_row = {"oid": 888, "defaclrole": 9, "defaclnamespace": 10,
                 "defaclobjtype": "r", "acl": [[9, 11, "SELECT", False]]}
    before["state"]["defaults"] = [before_row]
    after_state["defaults"] = [after_row]
    before["plan"]["before"] = [{k: v for k, v in before_row.items() if k != "oid"}]
    before["plan"]["after"] = snapshot.canonical_state(after_state)
    before["plan"]["full_after_sha256"] = policy.fingerprint(
        snapshot.canonical_state(after_state))
    after = deepcopy(before)
    after["state"] = after_state
    after["plan"] = {"after": snapshot.canonical_state(after_state),
                     "delta": {"relations": []}}
    assert snapshot.canonical_state(before["state"])["defaults"] == [
        {k: v for k, v in before_row.items() if k != "oid"}]
    assert snapshot.compare_post(before, after)["permission_enabled"] is False
    drift = deepcopy(after)
    drift["state"]["relations"].append({"oid": 9})
    with pytest.raises(snapshot.SnapshotError):
        snapshot.compare_post(before, drift)


def test_postgrant_default_rows_compare_in_logical_key_order_without_dropping_fields():
    before, after_state = _bundle()
    after_state["defaults"] = [
        {"oid": 700, "defaclrole": 9, "defaclnamespace": 30,
         "defaclobjtype": "r", "acl": [[9, 11, "SELECT", False]]},
        {"oid": 701, "defaclrole": 9, "defaclnamespace": 20,
         "defaclobjtype": "r", "acl": [[9, 11, "SELECT", False]]},
    ]
    physical = deepcopy(after_state)
    planned = deepcopy(after_state)
    for row in planned["defaults"]:
        row.pop("oid")
    planned["defaults"].sort(key=lambda row: (
        row["defaclrole"], row["defaclnamespace"], row["defaclobjtype"]))
    before["plan"]["after"] = planned
    before["plan"]["full_after_sha256"] = policy.fingerprint(planned)
    after = deepcopy(before)
    after["state"] = physical
    after["plan"] = {"after": planned, "delta": {"relations": []}}
    assert after["state"] == physical
    assert snapshot.compare_post(before, after)["permission_enabled"] is False
    assert after["state"] == physical
    for change in ("missing", "extra", "altered", "malformed_key", "malformed_type"):
        drift = deepcopy(after)
        if change == "missing":
            drift["state"]["defaults"].pop()
        elif change == "extra":
            drift["state"]["defaults"].append(
                {"oid": 702, "defaclrole": 9, "defaclnamespace": 40,
                 "defaclobjtype": "r", "acl": [[9, 11, "SELECT", False]]})
        elif change == "altered":
            drift["state"]["defaults"][0]["acl"] = []
        elif change == "malformed_key":
            drift["state"]["defaults"][0].pop("defaclrole")
        else:
            drift["state"]["defaults"][0]["defaclnamespace"] = "30"
        with pytest.raises(snapshot.SnapshotError,
                           match="INCOMPLETE_BROAD_READER_LIVE_SNAPSHOT"):
            snapshot.compare_post(before, drift)
