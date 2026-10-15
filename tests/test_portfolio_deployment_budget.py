"""Hermetic operation-specific deadlines; no database/cloud calls."""

from types import SimpleNamespace
import pytest
from mfdataindia.enrichment import portfolio_deployment as deployment, portfolio_schemas as schemas


@pytest.mark.parametrize("operation,expected", [("install", "420s"), ("publish", "180s")])
def test_owner_preservation_budget_does_not_change_append_deadlines_or_safety_checks(
    monkeypatch, operation, expected
):
    statements = []
    roles = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement):
            statements.append(statement)

    monkeypatch.setattr(deployment.psycopg, "connect", lambda *args, **kwargs: Connection())

    def pinned(manifest, environment, role):
        roles.append(role)
        return "never-connected"

    monkeypatch.setattr(deployment, "pinned_dsn", pinned)
    calls = []
    monkeypatch.setattr(
        deployment.portfolio_operations,
        "install",
        lambda conn, root: calls.append("full-preservation-install") or {"status": "INSTALLED"},
    )
    selected = schemas.Selection(
        source_id="a" * 64,
        identity_source_id="b" * 64,
        targets=[schemas.Target(code=1, sheet="BSLFEF", identity_page=28)],
    )
    monkeypatch.setattr(deployment, "selection", lambda *args: selected)
    monkeypatch.setattr(
        deployment.portfolio_store,
        "publish",
        lambda *args: (
            calls.append("bounded-publication")
            or [{"code": 1, "snapshot_id": "c" * 64, "holdings_count": 5}]
        ),
    )
    monkeypatch.setattr(deployment, "seal_evidence", lambda manifest, stage, value: value)
    result = deployment.execute(SimpleNamespace(), {}, operation)
    assert f"SET LOCAL transaction_timeout='{expected}'" in statements
    assert "SET LOCAL statement_timeout='120s'" in statements
    assert "SET LOCAL lock_timeout='5s'" in statements
    assert calls == [
        "full-preservation-install" if operation == "install" else "bounded-publication"
    ]
    assert roles == ["mfdata_admin" if operation == "install" else "mfdata_verified_generator"]
    assert result["source_rows_changed"] == 0
    assert deployment.INSTALL_TRANSACTION_SECONDS < 600
