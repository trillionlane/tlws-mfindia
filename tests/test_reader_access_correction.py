"""Exact synthetic profile/transaction tests; never connect to a provider."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg.pq import TransactionStatus

from mfdataindia.review import reader_access_correction as c
from mfdataindia.review.reader_access_profile_v1 import profile

ROOT = Path(__file__).resolve().parents[1]


def frozen_state():
    """Synthetic metadata shaped like frozen targets, not a live certificate."""
    p = profile()
    state = dict(relations=[], columns=[], roles=[], memberships=[], functions=[], defaults=[], policies=[],
                 namespaces=[dict(oid=16595, nspname="mf", owner=16587, acl=[], usage=True, create_allowed=False)],
                 databases=[dict(oid=1, datname="mfdataindia", owner=16587, acl=[], connect_allowed=True, create_allowed=False, temporary_allowed=True)])
    for name, oid in ((c.READER, 40032), (c.OWNER, 16587)):
        state["roles"].append(dict(oid=oid, rolname=name, rolsuper=False, rolinherit=name == c.OWNER,
            rolcreaterole=name == c.OWNER, rolcreatedb=name == c.OWNER, rolcanlogin=True,
            rolreplication=False, rolbypassrls=False, rolconfig=None))
    for item in p["objects"]:
        i, d = item["expected_identity"], item["relation_definition_and_security"]
        root = next(a for a in item["direct_relation_and_all_column_ACLs_before"] if a["object_kind"] == "relation")
        state["relations"].append(dict(oid=i["reloid"], namespace_oid=i["nspoid"], nspname="mf",
            relname=i["relname"], relkind=i["expected_kind"], owner=16587, attribute_count=d["attribute_count"],
            row_security=d["row_security"], force_row_security=d["force_row_security"], options=d["relation_options"],
            definition=d["private_view_definition"], acl=sorted(c._atom(a) for a in root["normalized_acl"]),
            capabilities=[False, False, False, False], sequence_capabilities=None))
        for old in item["all_physical_columns"]:
            row = {k: v for k, v in old.items() if k != "relation_oid"}
            row.update(oid=i["reloid"], acl=[], capabilities=[False, False, False, False])
            state["columns"].append(row)
    return state


def change():
    return c.prepare_change(frozen_state(), window_reference="SYNTHETIC_ADMIN_AND_RECURSIVE_LOCK_WINDOW")


def test_frozen_exact_84_plus_2_present_columns_no_alias_or_extra_profile():
    p = profile()
    assert c.fingerprint(p) == c.PROFILE_SHA256
    assert [len(i["columns_in_actual_attnum_order"]) for i in p["objects"]] == [84, 2]
    assert c._selected()["fund_variants"] == ["amfi_scheme_code", "group_key"]
    p["objects"].clear()
    assert len(profile()["objects"]) == 2
    assert not hasattr(c, "main")


def test_plan_is_pure_deepcopied_exact_delta_and_no_permission_authority():
    before = frozen_state()
    saved = deepcopy(before)
    value = c.prepare_change(before, window_reference="SYNTHETIC_WINDOW")
    assert before == saved
    assert value["runtime_or_write_approval"] is False
    assert sum(len(d["columns"]) for d in value["delta"]) == 86
    assert value["after"]["roles"] == before["roles"]
    assert all(not r["capabilities"][0] for r in value["after"]["relations"])
    value["before"]["roles"].clear()
    assert before == saved


@pytest.mark.parametrize("defect", ["oid", "owner", "namespace", "definition", "security", "column_type",
    "new_column", "missing_column", "column_acl", "root_acl", "role", "membership", "source_write",
    "schema_create", "db_create", "column_grant_option", "privileged_function"])
def test_each_identity_acl_type_security_or_privilege_drift_fails_closed(defect):
    state = frozen_state()
    if defect in ("oid", "owner"):
        state["relations"][0][defect] += 1
    elif defect == "namespace":
        state["relations"][0]["namespace_oid"] += 1
    elif defect == "definition":
        state["relations"][0]["definition"]["sha256"] = "0" * 64
    elif defect == "security":
        state["relations"][0]["options"] = ["security_invoker=true"]
    elif defect == "column_type":
        state["columns"][0]["type_oid"] += 1
    elif defect == "new_column":
        state["columns"].append({**state["columns"][0], "attnum": 85, "name": "future"})
    elif defect == "missing_column":
        state["columns"].pop(0)
    elif defect == "column_acl":
        state["columns"][0]["acl"] = [[16587, 40032, "SELECT", False]]
    elif defect == "root_acl":
        state["relations"][0]["acl"].append([16587, 40032, "SELECT", False])
    elif defect == "role":
        state["roles"][0]["rolinherit"] = True
    elif defect == "membership":
        state["memberships"].append({"member": 40032, "roleid": 16587})
    elif defect == "source_write":
        state["relations"][0]["capabilities"][2] = True
    elif defect == "schema_create":
        state["namespaces"][0]["create_allowed"] = True
    elif defect == "db_create":
        state["databases"][0]["create_allowed"] = True
    elif defect == "column_grant_option":
        state["columns"][0]["capabilities"][1] = True
    else:
        state["functions"] = [dict(proowner=16587, prosecdef=True, execute_allowed=True)]
    with pytest.raises(c.ChangeFailure):
        c.prepare_change(state, window_reference="SYNTHETIC_WINDOW")


@pytest.mark.parametrize("window", [None, "", " ", 1, False])
def test_no_implicit_administrative_or_lock_window(window):
    with pytest.raises(c.ChangeFailure):
        c.prepare_change(frozen_state(), window_reference=window)


class Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return deepcopy(self.row)


class Connection:
    autocommit = True
    info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

    def __init__(self, value, fail=None, final=None):
        self.value, self.fail, self.final = deepcopy(value), fail, final
        self.calls, self.snapshots = [], 0
        self.readonly = False

    def execute(self, statement, *, prepare):
        assert prepare is False
        statement = statement.as_string() if hasattr(statement, "as_string") else statement
        self.calls.append(statement)
        if statement == self.fail:
            raise RuntimeError("PRIVATE_DSN_SQL_SERVER_MESSAGE")
        if statement.startswith("BEGIN"):
            self.readonly = statement.endswith("READ ONLY")
        if statement == c.CONTEXT_SQL:
            return Cursor(dict(database="mfdataindia", principal=c.OWNER, session=c.OWNER, version=180006,
                isolation="read committed", readonly="on" if self.readonly else "off", statement_timeout="5s",
                lock_timeout="5s", transaction_timeout="15s", search_path="pg_catalog", timezone="UTC"))
        if statement == c.STATE_SQL:
            self.snapshots += 1
            state = self.value["before"] if self.snapshots == 1 else self.value["after"]
            if self.snapshots == 3 and self.final is not None:
                state = self.final
            return Cursor({"value": state})
        return Cursor(None)


def test_original_success_uses_two_fixed_column_grants_and_final_readonly_verification():
    doc = change()
    conn = Connection(doc)
    permit = c.Permit(doc, c.fingerprint(doc))
    result = c.run_change(conn, permit)
    assert permit.used and result["stage"] == "COMMITTED_VERIFIED"
    grants = [s for s in conn.calls if s.startswith("GRANT")]
    assert len(grants) == 2 and all("SELECT (" in s and "WITH GRANT OPTION" not in s for s in grants)
    assert conn.calls.count("COMMIT") == 1 and conn.calls[-1] == "ROLLBACK"
    assert "BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY" in conn.calls
    with pytest.raises(c.ChangeFailure, match="CONSUMED"):
        c.run_change(conn, permit)


@pytest.mark.parametrize("defect", ["wrong_fingerprint", "extra_key", "grant_option", "extra_column", "scope",
                                    "lifetime", "database", "engine_hash", "profile_hash"])
def test_unapproved_or_expanded_inputs_make_zero_connection_calls(defect):
    doc = change()
    if defect == "extra_key":
        doc["override"] = True
    elif defect == "grant_option":
        doc["operation"] = "GRANT WITH GRANT OPTION"
    elif defect == "extra_column":
        doc["delta"][1]["columns"].append("base_scheme_name")
    elif defect == "scope":
        doc["reader"] = "mfdata_app"
    elif defect == "lifetime":
        doc["lifetime"] = "TEMPORARY"
    elif defect == "database":
        doc["database"] = "production"
    elif defect == "engine_hash":
        doc["engine_sha256"] = "0" * 64
    elif defect == "profile_hash":
        doc["profile_sha256"] = "0" * 64
    permit = c.Permit(doc, "0" * 64 if defect == "wrong_fingerprint" else c.fingerprint(doc))
    conn = Connection(doc)
    with pytest.raises(c.ChangeFailure):
        c.run_change(conn, permit)
    assert conn.calls == []


def test_precommit_failure_rolls_back_once_and_never_prints_server_message():
    doc = change()
    conn = Connection(doc, fail=c.STATE_SQL)
    with pytest.raises(c.ChangeFailure, match="PRECOMMIT_ABORTED_NO_RETRY") as error:
        c.run_change(conn, c.Permit(doc, c.fingerprint(doc)))
    assert "PRIVATE" not in str(error.value)
    assert conn.calls.count("ROLLBACK") == 1 and "COMMIT" not in conn.calls


def test_commit_ambiguity_never_rolls_back_reverses_or_retries():
    doc = change()
    conn = Connection(doc, fail="COMMIT")
    with pytest.raises(c.ChangeFailure, match="COMMIT_OUTCOME_UNKNOWN"):
        c.run_change(conn, c.Permit(doc, c.fingerprint(doc)))
    assert conn.calls.count("COMMIT") == 1 and "ROLLBACK" not in conn.calls


def test_committed_but_final_drift_is_not_success_or_automatic_reversal():
    doc = change()
    conn = Connection(doc, final=doc["before"])
    with pytest.raises(c.ChangeFailure, match="COMMITTED_UNVERIFIED"):
        c.run_change(conn, c.Permit(doc, c.fingerprint(doc)))
    assert not any(s.startswith("REVOKE") for s in conn.calls)
    assert conn.calls.count("COMMIT") == 1


def test_separate_exact_reversal_preserves_receipt_and_needs_new_permit():
    doc = change()
    receipt = c.run_change(Connection(doc), c.Permit(doc, c.fingerprint(doc)))
    reverse = c.prepare_reversal(receipt, window_reference="SYNTHETIC_NEW_REVERSAL_WINDOW")
    saved = deepcopy(receipt)
    conn = Connection(reverse)
    result = c.run_change(conn, c.Permit(reverse, c.fingerprint(reverse)), reversal_receipt=receipt)
    assert receipt == saved and result["stage"] == "COMMITTED_VERIFIED"
    revokes = [s for s in conn.calls if s.startswith("REVOKE")]
    assert len(revokes) == 2 and all(s.endswith(" RESTRICT") and "ALL" not in s and "CASCADE" not in s for s in revokes)


def test_future_provisioning_never_silently_admits_or_reapplies_broad_reconciliation():
    doc = change()
    raw = (ROOT / "scripts/sql/reconcile_verified_review_privileges.sql").read_bytes()
    c.require_provisioning_admission(doc["after"], reconciler_bytes=raw, explicitly_selected_profile=c.PROFILE_SHA256)
    for state, selected, data in ((doc["before"], c.PROFILE_SHA256, raw), (doc["after"], None, raw),
                                   (doc["after"], c.PROFILE_SHA256, raw + b"\n")):
        with pytest.raises(c.ChangeFailure):
            c.require_provisioning_admission(state, reconciler_bytes=data, explicitly_selected_profile=selected)
    assert hashlib.sha256(raw).hexdigest() == c.RECONCILER_SHA256


def test_unknown_or_mutated_receipt_cannot_authorize_reversal():
    with pytest.raises(c.ChangeFailure):
        c.prepare_reversal({"stage": "COMMIT_OUTCOME_UNKNOWN"}, window_reference="SYNTHETIC")


def test_source_is_dormant_no_secret_fetch_connection_cli_installer_or_query_rewrite():
    source = Path(c.__file__).read_text()
    assert "psycopg.connect(" not in source and "os.environ" not in source and "subprocess" not in source
    assert "install_and_reconcile(" not in source and "__main__" not in source
    assert "SELECT * FROM mf." not in c.STATE_SQL


def test_spent_wall_budget_does_not_start_unbounded_cleanup_io(monkeypatch):
    doc = change()
    conn = Connection(doc)

    def expired(_):
        raise c.WallTimeout("WHOLE_OPERATION_15S_TIMEOUT")

    monkeypatch.setattr(c, "_snapshot", expired)
    with pytest.raises(c.ChangeFailure, match="PRECOMMIT_ABORT_UNCONFIRMED_WALL_TIMEOUT"):
        c.run_change(conn, c.Permit(doc, c.fingerprint(doc)))
    assert "COMMIT" not in conn.calls and "ROLLBACK" not in conn.calls


def test_deadline_covers_rollback_and_never_retries_unconfirmed_cleanup(monkeypatch):
    doc = change()
    conn = Connection(doc, fail=c.STATE_SQL)
    original = conn.execute

    def execute(statement, *, prepare):
        if statement == "ROLLBACK":
            conn.calls.append(statement)
            assert c.signal.getitimer(c.signal.ITIMER_REAL)[0] > 0
            raise c.WallTimeout("WHOLE_OPERATION_15S_TIMEOUT")
        return original(statement, prepare=prepare)

    monkeypatch.setattr(conn, "execute", execute)
    with pytest.raises(c.ChangeFailure, match="PRECOMMIT_ROLLBACK_UNCONFIRMED"):
        c.run_change(conn, c.Permit(doc, c.fingerprint(doc)))
    assert conn.calls.count("ROLLBACK") == 1 and "COMMIT" not in conn.calls
