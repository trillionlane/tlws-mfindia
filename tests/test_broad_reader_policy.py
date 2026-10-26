"""Provider-free fixed-domain policy and exact owner-transaction mechanics."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from psycopg.pq import TransactionStatus

from mfdataindia.review import broad_reader_policy as p
from test_reader_access_correction import frozen_state


def state():
    return frozen_state()  # Synthetic complete catalogue; never live evidence.


@pytest.mark.parametrize("schema", p.SCHEMAS)
def test_schema_usage_must_not_unlock_a_preexisting_privileged_function(schema):
    s = state()
    namespace = s["namespaces"][0]["oid"]
    if schema != "mf":
        namespace += 1
        s["namespaces"].append(
            dict(
                oid=namespace,
                nspname=schema,
                owner=16587,
                acl=[],
                usage=False,
                create_allowed=False,
            )
        )
    s["functions"] = [
        dict(
            oid=90001,
            pronamespace=namespace,
            proname="owner_mutation",
            proowner=16587,
            prosecdef=True,
            prokind="f",
            proargtypes="",
            prorettype=2278,
            proconfig=deepcopy(s["roles"][0]["rolconfig"]),
            private_body_sha256="0" * 64,
            acl=[[16587, 0, "EXECUTE", False]],
            execute_allowed=True,
        )
    ]
    with pytest.raises(p.PolicyError, match="PRIVILEGED_.*FUNCTION"):
        p.prepare(s)


@pytest.mark.parametrize("grantee", [0, 40032])
@pytest.mark.parametrize(
    "kind,privilege", [("r", "UPDATE"), ("r", "SELECT"), ("S", "USAGE"), ("n", "CREATE")]
)
def test_existing_future_write_or_grant_option_is_not_misreported_read_only(
    grantee, kind, privilege
):
    s = state()
    s["defaults"] = [
        dict(
            oid=901,
            defaclrole=16587,
            defaclnamespace=0,
            defaclobjtype=kind,
            acl=[[16587, grantee, privilege, privilege == "SELECT"]],
        )
    ]
    with pytest.raises(p.PolicyError, match="FUTURE_WRITE_OR_GRANT_OPTION"):
        p.prepare(s)


def window():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return dict(
        starts_at=(now - timedelta(seconds=10)).isoformat(),
        ends_at=(now + timedelta(minutes=5)).isoformat(),
        exclusive_administration=True,
        data_schema_custody=True,
        future_owner_provisioning=True,
    )


def permit(plan=None):
    doc = p.approval_document(plan or p.prepare(state()), window=window())
    return p.Permit(doc, p.fingerprint(doc))


class Connection:
    """Fixed offline fake; production admission is not overridden."""

    autocommit = True
    info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

    def __init__(self, *, failure=None, rollback_failure=None, drift=None):
        self.plan = p.prepare(state())
        self.calls, self.snapshots = [], 0
        self.failure, self.rollback_failure, self.drift = failure, rollback_failure, drift
        self.readonly = False

    def execute(self, statement, *, prepare):
        assert prepare is False
        self.calls.append(statement)
        if statement == "ROLLBACK" and self.rollback_failure is not None:
            raise self.rollback_failure
        if statement == self.failure:
            raise RuntimeError("private-server-password-must-not-leak")
        if statement.startswith("BEGIN"):
            self.readonly = "READ ONLY" in statement
        if statement == p.CONTEXT_SQL:
            result = dict(
                database=p.DATABASE,
                principal=p.OWNER,
                session=p.OWNER,
                version=180006,
                isolation="read committed",
                readonly="on" if self.readonly else "off",
                statement_timeout="5s",
                lock_timeout="5s",
                transaction_timeout="15s",
                search_path="pg_catalog",
                timezone="UTC",
            )
        elif statement == p.STATE_SQL:
            result = dict(
                value=deepcopy(self.plan["before"] if self.snapshots == 0 else self.plan["after"])
            )
            if self.drift == self.snapshots:
                result["value"]["databases"][0]["temporary_allowed"] = False
            self.snapshots += 1
        else:
            result = None
        return SimpleNamespace(fetchone=lambda: result)


def test_fixed_profile_whole_schemas_not_frozen_84_plus_2_or_global_read_role():
    profile = p.profile()
    assert p.PROFILE_SHA256 == "5519d0af8782e18d9a3ef81494d2fec4c002f43a2f079a1310f0770f6dc15edf"
    assert len(profile["schemas"]) == 16 and profile["schemas"] == sorted(profile["schemas"])
    assert profile["all_rows_all_reader_consumers"] and not profile["writes"]
    assert not {"public", "pg_catalog", "information_schema"} & set(profile["schemas"])
    profile["schemas"].clear()
    assert len(p.profile()["schemas"]) == 16
    assert not hasattr(p, "main") and not hasattr(p, "connect")


def test_pure_plan_does_not_mutate_source_and_adds_table_reads_and_scoped_defaults():
    before = state()
    saved = deepcopy(before)
    plan = p.prepare(before)
    assert before == saved and plan["runtime_or_human_approval"] is False
    assert len(plan["delta"]["relations"]) == 2
    assert plan["coverage"]["present_schemas"] == ["mf"]
    assert len(plan["coverage"]["absent_schemas"]) == 15
    assert (
        plan["sql"][-1]
        == 'ALTER DEFAULT PRIVILEGES FOR ROLE "mfdata_admin" IN SCHEMA "mf" GRANT SELECT ON TABLES TO "mfdata_verified_reader"'
    )
    assert all(
        "SELECT (" not in command and "WITH GRANT OPTION" not in command for command in plan["sql"]
    )
    assert all(r["capabilities"] == [True, False, False, True] for r in plan["after"]["relations"])
    assert all(c["capabilities"] == [True, False, False, False] for c in plan["after"]["columns"])
    assert p.prepare(plan["after"])["sql"] == []
    assert p.require_current_policy(plan["after"])["future_creator"] == p.OWNER
    with pytest.raises(p.PolicyError, match="NOT_ENROLLED"):
        p.require_current_policy(before)


@pytest.mark.parametrize(
    "defect",
    [
        "schema",
        "schema_owner",
        "object_owner",
        "foreign",
        "rls",
        "force_rls",
        "root_write",
        "root_grant_option",
        "column_write",
        "column_grant_option",
        "membership",
        "superuser",
        "role_inherit",
        "schema_create",
        "db_create",
        "missing_column",
        "duplicate_column",
        "malformed_acl_oid",
        "raw_private_setting",
        "unknown_key",
        "duplicate_default",
    ],
)
def test_unsafe_or_incomplete_catalogue_rejected_before_plan(defect):
    s = state()
    if defect == "schema":
        s["relations"][0]["nspname"] = "public"
    elif defect == "schema_owner":
        s["namespaces"][0]["owner"] += 1
    elif defect == "object_owner":
        s["relations"][0]["owner"] += 1
    elif defect == "foreign":
        s["relations"][0]["relkind"] = "f"
    elif defect == "rls":
        s["relations"][0]["row_security"] = True
    elif defect == "force_rls":
        s["relations"][0]["force_row_security"] = True
    elif defect == "root_write":
        s["relations"][0]["capabilities"][2] = True
    elif defect == "root_grant_option":
        s["relations"][0]["capabilities"][1] = True
    elif defect == "column_write":
        s["columns"][0]["capabilities"][2] = True
    elif defect == "column_grant_option":
        s["columns"][0]["capabilities"][1] = True
    elif defect == "membership":
        s["memberships"] = [
            dict(
                roleid=16587,
                member=40032,
                grantor=16587,
                admin_option=False,
                inherit_option=True,
                set_option=False,
            )
        ]
    elif defect == "superuser":
        s["roles"][0]["rolsuper"] = True
    elif defect == "role_inherit":
        s["roles"][0]["rolinherit"] = True
    elif defect == "schema_create":
        s["namespaces"][0]["create_allowed"] = True
    elif defect == "db_create":
        s["databases"][0]["create_allowed"] = True
    elif defect == "missing_column":
        s["columns"].pop()
    elif defect == "duplicate_column":
        s["columns"].append(deepcopy(s["columns"][0]))
    elif defect == "malformed_acl_oid":
        s["columns"][0]["acl"] = [["16587", 40032, "SELECT", False]]
    elif defect == "raw_private_setting":
        s["roles"][0]["rolconfig"] = ["private.password=fixture"]
    elif defect == "unknown_key":
        s["arbitrary"] = []
    else:
        row = dict(oid=100, defaclrole=16587, defaclnamespace=16595, defaclobjtype="r", acl=[])
        s["defaults"] = [row, {**row, "oid": 101}]
    with pytest.raises(p.PolicyError):
        p.prepare(s)


def test_default_row_oid_only_normalization_preserves_existing_global_and_other_role_atoms():
    s = state()
    s["defaults"] = [
        dict(
            oid=100,
            defaclrole=16587,
            defaclnamespace=0,
            defaclobjtype="r",
            acl=[[16587, 123, "SELECT", False]],
        ),
        dict(
            oid=101,
            defaclrole=16587,
            defaclnamespace=16595,
            defaclobjtype="r",
            acl=[[16587, 123, "UPDATE", False]],
        ),
    ]
    plan = p.prepare(s)
    assert plan["after"]["defaults"][0] == p.catalogue(s)["defaults"][0]
    assert [16587, 123, "UPDATE", False] in plan["after"]["defaults"][1]["acl"]
    changed = deepcopy(s)
    changed["defaults"][0]["oid"] += 1000
    assert p.catalogue(changed) == p.catalogue(s)
    changed["defaults"][0]["acl"][0][0] += 1
    assert p.catalogue(changed) != p.catalogue(s)
    assert plan["delta"]["defaults"][0]["preexisting"] is True


def test_preexisting_direct_column_and_other_grantor_acl_are_never_revoked():
    s = state()
    s["columns"][0]["acl"] = [[16587, 40032, "SELECT", False]]
    s["relations"][0]["acl"].append([999, 123, "SELECT", False])
    s["relations"][0]["acl"].sort()
    plan = p.prepare(s)
    columns = {(c["oid"], c["attnum"]): c for c in plan["after"]["columns"]}
    relations = {r["oid"]: r for r in plan["after"]["relations"]}
    original_col = s["columns"][0]
    assert columns[(original_col["oid"], original_col["attnum"])]["acl"] == original_col["acl"]
    assert all(a in relations[s["relations"][0]["oid"]]["acl"] for a in s["relations"][0]["acl"])
    assert not any("REVOKE" in command for command in plan["sql"])


def test_public_apply_exact_bound_single_use_owner_context_and_success_receipt():
    conn, token = Connection(), permit()
    saved = deepcopy(token.document)
    result = p.apply(conn, token)
    assert result["stage"] == "COMMITTED_VERIFIED" and token.used
    assert token.document == saved
    assert conn.calls.count("COMMIT") == conn.calls.count("ROLLBACK") == 1
    assert conn.calls[-1] == "ROLLBACK" and conn.snapshots == 3
    assert result["defaults_reversal_does_not_revoke_inherited_object_grants"] is True
    assert result["performance_or_FS13_acceptance"] is False
    assert result["receipt_sha256"] == p.fingerprint(
        {k: v for k, v in result.items() if k != "receipt_sha256"}
    )
    with pytest.raises(p.PolicyError, match="CONSUMED"):
        p.apply(conn, token)


@pytest.mark.parametrize("defect", ["plan", "fingerprint", "expired", "custody", "not_idle"])
def test_binding_or_administrative_window_failure_performs_no_database_io(defect):
    conn, token = Connection(), permit()
    if defect == "plan":
        token.document["plan"]["sql"].append("SELECT private")
        token.approved_fingerprint = p.fingerprint(token.document)
    elif defect == "fingerprint":
        token.approved_fingerprint = "0" * 64
    elif defect == "expired":
        token.document["window"].update(
            starts_at="2020-01-01T00:00:00+00:00", ends_at="2020-01-01T00:01:00+00:00"
        )
        token.approved_fingerprint = p.fingerprint(token.document)
    elif defect == "custody":
        token.document["window"]["data_schema_custody"] = False
        token.approved_fingerprint = p.fingerprint(token.document)
    else:
        conn.info = SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
    with pytest.raises(p.PolicyError):
        p.apply(conn, token)
    assert token.used and conn.calls == []


@pytest.mark.parametrize("phase", [0, 1, 2])
def test_fresh_before_precommit_and_final_drift_never_admitted(phase):
    conn = Connection(drift=phase)
    expected = "COMMITTED_UNVERIFIED" if phase == 2 else "PRECOMMIT_ABORTED_NO_RETRY"
    with pytest.raises(p.PolicyError, match=expected):
        p.apply(conn, permit())
    assert conn.calls.count("COMMIT") == (1 if phase == 2 else 0)
    if phase == 2:
        assert conn.calls[-1] == p.STATE_SQL
    else:
        assert conn.calls[-1] == "ROLLBACK"


@pytest.mark.parametrize(
    "cleanup", [RuntimeError("private rollback text"), p.WallExpired("private timeout text")]
)
def test_original_failure_and_cleanup_failure_preserve_unconfirmed_abort(cleanup):
    conn = Connection(failure=p.prepare(state())["sql"][0], rollback_failure=cleanup)
    with pytest.raises(p.PolicyError) as error:
        p.apply(conn, permit())
    assert "ABORT_UNCONFIRMED" in str(error.value) and "private" not in str(error.value)
    assert conn.calls.count("ROLLBACK") == 1 and "COMMIT" not in conn.calls


def test_commit_unknown_has_no_retry_or_rollback_and_no_private_text():
    conn = Connection(failure="COMMIT")
    with pytest.raises(p.PolicyError, match="^COMMIT_OUTCOME_UNKNOWN$") as error:
        p.apply(conn, permit())
    assert error.value.__cause__ is None and conn.calls[-1] == "COMMIT"
    assert conn.calls.count("COMMIT") == 1 and "ROLLBACK" not in conn.calls


@pytest.mark.parametrize("bad", [None, 42, "not-a-row", [1]])
def test_malformed_default_rows_rejected_as_fixed_policy_error(bad):
    s = state()
    s["defaults"] = [bad]
    with pytest.raises(p.PolicyError):
        p.prepare(s)


def test_wall_expiry_during_catalogue_read_never_sends_cleanup_sql():
    conn = Connection()
    original = conn.execute

    def execute(statement, **kwargs):
        if statement == p.STATE_SQL:
            conn.calls.append(statement)
            raise p.WallExpired("expired")
        return original(statement, **kwargs)

    conn.execute = execute
    with pytest.raises(p.PolicyError, match="WALL_ABORT_UNCONFIRMED_NO_FURTHER_IO"):
        p.apply(conn, permit())
    assert conn.calls[-1] == p.STATE_SQL and "ROLLBACK" not in conn.calls
