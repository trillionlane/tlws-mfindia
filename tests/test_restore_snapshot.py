from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).parents[1] / "scripts" / "restore_snapshot.py"
    spec = importlib.util.spec_from_file_location("restore_snapshot", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_postgres_env_accepts_only_approved_cloud_sql_socket(monkeypatch):
    restore = _module()
    monkeypatch.setenv("UNRELATED_VALUE", "preserved")
    env = restore.postgres_env(
        "postgresql://mfdata_admin:p%40ss@/mfdataindia?"
        "host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev"
    )
    assert env["PGHOST"] == "/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev"
    assert env["PGDATABASE"] == "mfdataindia"
    assert env["PGUSER"] == "mfdata_admin"
    assert env["PGPASSWORD"] == "p@ss"
    assert env["UNRELATED_VALUE"] == "preserved"


@pytest.mark.parametrize(
    "dsn",
    [
        "mysql://mfdata_admin:secret@/mfdataindia?host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev",
        "postgresql://mfdata_admin:secret@/other?host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev",
        "postgresql://mfdata_admin:secret@/mfdataindia?host=/cloudsql/trillionlane-dev:asia-south1:other",
        "postgresql://mfdata_admin@/mfdataindia?host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev",
    ],
)
def test_postgres_env_rejects_non_contract_dsn(dsn):
    restore = _module()
    with pytest.raises(RuntimeError):
        restore.postgres_env(dsn)
