"""No hidden acquisition/install on help; reject unpinned databases before connect."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from mfdataindia.review.rollout_cloud import (
    ENRICHMENT_FACTORY,
    HISTORICAL_FACTORY,
    api_args,
    revision_name,
)
from test_verified_review_rollout import manifest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/official_enrichment_evidence.py"


def module():
    spec = importlib.util.spec_from_file_location("official_enrichment_cli_under_test", SCRIPT)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_help_is_dormant_and_fixed_profile_changes_no_prior_profile():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0 and "discover" in result.stdout
    assert api_args(ENRICHMENT_FACTORY)[0] == ENRICHMENT_FACTORY
    assert api_args(HISTORICAL_FACTORY)[0] == HISTORICAL_FACTORY
    assert revision_name(manifest(), ENRICHMENT_FACTORY).endswith("-enrichment")
    assert revision_name(manifest(), HISTORICAL_FACTORY).endswith("-historical")


@pytest.mark.parametrize(
    "dsn",
    [
        "host=127.0.0.1 dbname=mfdataindia user=mfdata_admin password=fixture",
        "host=/cloudsql/trillionlane-prod:asia-south1:tlws-mf-data-prod dbname=mfdataindia user=mfdata_admin password=fixture",
        "host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev dbname=wrong user=mfdata_admin password=fixture",
        "host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev dbname=mfdataindia user=postgres password=fixture",
        "host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev hostaddr=127.0.0.1 dbname=mfdataindia user=mfdata_admin password=fixture",
    ],
)
def test_unpinned_live_install_never_connects(monkeypatch, dsn):
    operator = module()
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "install"])
    monkeypatch.setattr(operator, "resolve_dsn", lambda *a, **k: dsn)
    monkeypatch.setattr(
        operator.psycopg, "connect", lambda *a, **k: pytest.fail("must not connect")
    )
    with pytest.raises(ValueError, match="pinned DEV"):
        operator.main()


def test_bounded_file_rejects_empty_and_oversized_bytes(tmp_path):
    operator = module()
    path = tmp_path / "fixture-only.html"
    path.write_bytes(b"12345")
    with pytest.raises(ValueError, match="size"):
        operator.bounded_file(path, 4)
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="size"):
        operator.bounded_file(path, 4)
    with pytest.raises(ValueError, match="regular"):
        operator.bounded_file(tmp_path, 4)
