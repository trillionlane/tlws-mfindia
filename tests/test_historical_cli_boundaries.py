import importlib.util
from pathlib import Path

import pytest
from mfdataindia.calculators.historical_certification import certify, scenarios
from mfdataindia.review.rollout import CONNECTION, verify_evidence
from test_verified_review_rollout import manifest
from test_historical_calculators import sample

ROOT = Path(__file__).resolve().parents[1]


def installer():
    spec = importlib.util.spec_from_file_location(
        "historical_installer_fixture", ROOT / "scripts/install_historical_calculator_policy_dev.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixed_owner_entrypoint_has_no_per_gate_mode_variables(monkeypatch):
    module = installer()
    m = manifest()
    calls = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, text):
            calls.append(text)

    monkeypatch.setattr(module.psycopg, "connect", lambda *a, **k: Conn())
    monkeypatch.setattr(
        module, "install_policy", lambda c: {"policy_hash": "a" * 64, "source_writes": 0}
    )
    environment = {
        "DEPLOYMENT_GIT_SHA": m.git_sha,
        "MFDATAINDIA_DSN": f"host='/cloudsql/{CONNECTION}' dbname=mfdataindia user=mfdata_admin password=fixture",
    }
    result = verify_evidence(m, "historical-policy-install", module.execute(m, environment))
    assert (
        result["source_writes"] == 0
        and calls[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
    )
    assert (
        "HISTORICAL_OPERATION"
        not in (ROOT / "scripts/install_historical_calculator_policy_dev.py").read_text()
    )


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://reader:fixture@127.0.0.1/mfdataindia",
        f"host='/cloudsql/{CONNECTION}' dbname=wrong user=mfdata_verified_reader password=fixture",
        f"host='/cloudsql/{CONNECTION}' dbname=mfdataindia user=mfdata_admin password=fixture",
    ],
)
def test_certifier_rejects_wrong_target_before_secrets_or_http(dsn):
    with pytest.raises(ValueError, match="pinned DEV reader"):
        certify(manifest(), {"MFDATAINDIA_DSN": dsn})


@pytest.mark.parametrize(
    "changes",
    [{"user": "reader"}, {"dbname": "wrong"}, {"host": "127.0.0.1"}, {"hostaddr": "127.0.0.1"}],
)
def test_actual_owner_installer_rejects_wrong_dsn_before_connect(monkeypatch, changes):
    module = installer()
    m = manifest()
    from psycopg.conninfo import make_conninfo

    fields = {
        "host": "/cloudsql/" + CONNECTION,
        "dbname": "mfdataindia",
        "user": "mfdata_admin",
        "password": "fixture",
        **changes,
    }

    def forbidden(*a, **k):
        raise AssertionError("must reject before connect")

    monkeypatch.setattr(module.psycopg, "connect", forbidden)
    with pytest.raises(ValueError, match="pinned DEV source"):
        module.execute(
            m, {"DEPLOYMENT_GIT_SHA": m.git_sha, "MFDATAINDIA_DSN": make_conninfo(**fields)}
        )


def test_bounded_nonpersonal_historical_cases_use_actual_snapshot_last_date():
    source = sample("2023-03-01", "2024-03-01")
    values = scenarios(source)
    assert {v["kind"] for v in values} == {"sip", "lumpsum"}
    assert all(v["end_date"] == "2024-03-01" and v["start_date"] == "2023-03-01" for v in values)
    assert (
        "recovery=True"
        in (ROOT / "scripts/certify_historical_calculators_recovery_dev.py").read_text()
    )
