import importlib.util
from pathlib import Path

import pytest

from mfdataindia.calculators.certification import certify, scenarios
from mfdataindia.review.rollout import CONNECTION, verify_evidence
from test_verified_review_rollout import manifest

ROOT = Path(__file__).resolve().parents[1]


def installer():
    spec = importlib.util.spec_from_file_location(
        "forward_installer_fixture", ROOT / "scripts/install_forward_calculator_policy_dev.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_install_entrypoint_uses_fixed_owner_and_preserves_no_mode_semantics(monkeypatch):
    module = installer()
    m = manifest()
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, text):
            calls.append(text)

    monkeypatch.setattr(module.psycopg, "connect", lambda *a, **k: Connection())
    monkeypatch.setattr(
        module,
        "install_policy",
        lambda c: {
            "policy_hash": "a" * 64,
            "source_writes": 0,
            "schema_changes": 0,
            "personal_scenarios_persisted": 0,
        },
    )
    env = {
        "DEPLOYMENT_GIT_SHA": m.git_sha,
        "MFDATAINDIA_DSN": f"host='/cloudsql/{CONNECTION}' dbname=mfdataindia user=mfdata_admin password=fixture",
    }
    result = verify_evidence(m, "forward-policy-install", module.execute(m, env))
    assert result["source_writes"] == 0 and result["schema_changes"] == 0
    assert calls[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"


@pytest.mark.parametrize(
    "dsn",
    [
        "host='127.0.0.1' dbname=mfdataindia user=mfdata_admin password=fixture",
        f"host='/cloudsql/{CONNECTION}' dbname=other user=mfdata_admin password=fixture",
        f"host='/cloudsql/{CONNECTION}' dbname=mfdataindia user=mfdata_verified_reader password=fixture",
    ],
)
def test_installer_rejects_wrong_target_before_connect(monkeypatch, dsn):
    module = installer()
    m = manifest()

    def forbidden(*a, **k):
        raise AssertionError("must not connect")

    monkeypatch.setattr(module.psycopg, "connect", forbidden)
    with pytest.raises(ValueError, match="fixed DEV database/owner"):
        module.execute(m, {"DEPLOYMENT_GIT_SHA": m.git_sha, "MFDATAINDIA_DSN": dsn})


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://reader:fixture@127.0.0.1/mfdataindia",
        f"host='/cloudsql/{CONNECTION}' dbname=mfdataindia user=mfdata_admin password=fixture",
    ],
)
def test_certifier_rejects_wrong_reader_before_secret_or_http(dsn):
    with pytest.raises(ValueError, match="fixed DEV reader"):
        certify(manifest(), {"MFDATAINDIA_DSN": dsn})


def test_certification_scenarios_are_nonpersonal_and_cover_all_declared_engines():
    values = scenarios()
    assert {v["kind"] for v in values} == {"lumpsum", "sip", "stepup_sip", "swp", "goal"}
    assert len(values) == 8 and all("customer_name" not in v for v in values)


def test_fixed_recovery_entrypoint_does_not_introduce_environment_gate_modes():
    text = (ROOT / "scripts/certify_forward_calculators_recovery_dev.py").read_text()
    assert "certify(m, os.environ, recovery=True)" in text
    assert "FORWARD_OPERATION" not in text and "CALCULATOR_OPERATION" not in text
