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
    before["state"]["defaults"] = [{"oid": 777, "creator": 9}]
    after_state["defaults"] = [{"oid": 888, "creator": 9, "acl": "SELECT"}]
    before["plan"]["before"] = [{"creator": 9}]
    before["plan"]["after"] = snapshot.canonical_state(after_state)
    before["plan"]["full_after_sha256"] = policy.fingerprint(
        snapshot.canonical_state(after_state))
    after = deepcopy(before)
    after["state"] = after_state
    after["plan"] = {"after": snapshot.canonical_state(after_state),
                     "delta": {"relations": []}}
    assert snapshot.canonical_state(before["state"])["defaults"] == [{"creator": 9}]
    assert snapshot.compare_post(before, after)["permission_enabled"] is False
    drift = deepcopy(after)
    drift["state"]["relations"].append({"oid": 9})
    with pytest.raises(snapshot.SnapshotError):
        snapshot.compare_post(before, drift)
