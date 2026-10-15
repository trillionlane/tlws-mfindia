"""Hermetic approval, operation scope and separate installation/append budgets."""

from types import SimpleNamespace
import pytest
from mfdataindia.analytics import overlap_deployment as deployment, overlap_schemas as schemas
from mfdataindia.review.policy import fingerprint


def pair():
    return schemas.Pair(
        left_code=1, right_code=2, left_snapshot_id="a" * 64, right_snapshot_id="b" * 64
    )


@pytest.mark.parametrize("fault", ["hash", "cohort", "extra", "reverse"])
def test_selection_binding_cannot_expand_scope(fault):
    value = pair()
    environment = {
        "OVERLAP_PAIR": value.model_dump_json(),
        "OVERLAP_PAIR_HASH": fingerprint(value.model_dump(mode="json")),
    }
    manifest = SimpleNamespace(cohort=[1, 2])
    if fault == "hash":
        environment["OVERLAP_PAIR_HASH"] = "0" * 64
    elif fault == "cohort":
        manifest.cohort = [1]
    elif fault == "extra":
        environment["OVERLAP_PAIR"] = value.model_dump_json()[:-1] + ',"unapproved":true}'
    else:
        environment["OVERLAP_PAIR"] = value.model_dump_json().replace(
            '"left_code":1', '"left_code":3'
        )
    with pytest.raises(ValueError):
        deployment.selection(manifest, environment)


@pytest.mark.parametrize(
    "operation,seconds,role",
    [("install", 420, "mfdata_admin"), ("publish", 180, "mfdata_verified_generator")],
)
def test_approval_and_privilege_budget_before_operations(monkeypatch, operation, seconds, role):
    statements, roles, calls = [], [], []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement):
            statements.append(statement)

    monkeypatch.setattr(deployment.psycopg, "connect", lambda *a, **kw: Connection())
    monkeypatch.setattr(deployment, "pinned_dsn", lambda m, e, r: roles.append(r) or "no-network")
    monkeypatch.setattr(deployment, "selection", lambda *args: calls.append("approval") or pair())
    monkeypatch.setattr(
        deployment.overlap_operations,
        "install",
        lambda *args: calls.append("full-preservation-install") or {"status": "INSTALLED"},
    )
    monkeypatch.setattr(
        deployment.overlap_store,
        "publish",
        lambda *args: (
            calls.append("one-pair-publish")
            or {
                "result_id": "c" * 64,
                "observed_overlap_percent": "10.00000000",
                "shared_security_count": 1,
            }
        ),
    )
    monkeypatch.setattr(deployment, "seal_evidence", lambda m, s, v: v)
    result = deployment.execute(SimpleNamespace(), {}, operation)
    assert roles == [role] and calls == [
        "approval",
        "full-preservation-install" if operation == "install" else "one-pair-publish",
    ]
    assert f"SET LOCAL transaction_timeout='{seconds}s'" in statements
    assert "SET LOCAL statement_timeout='120s'" in statements
    assert "SET LOCAL lock_timeout='5s'" in statements
    assert result["source_rows_changed"] == 0


def test_no_source_import_operation():
    with pytest.raises(ValueError):
        deployment.execute(SimpleNamespace(), {}, "import")
