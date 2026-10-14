from copy import deepcopy

import pytest

from mfdataindia.analytics import rolling, rolling_operations as operations
from mfdataindia.analytics.rolling_certification import certify
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import seal_evidence
from mfdataindia.review.rollout_cloud import ROLLING_FACTORY, api_args, revision_name
from test_verified_review_rollout import manifest, revision


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://mfdata_verified_reader:fixture@127.0.0.1/mfdataindia",
        "host='/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev' dbname=other user=mfdata_verified_reader password=fixture",
        "host='/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev' dbname=mfdataindia user=mfdata_admin password=fixture",
    ],
)
def test_certification_wrong_database_is_rejected_before_secret_or_request(dsn):
    with pytest.raises(ValueError, match="pinned DEV database"):
        certify(manifest(), {"MFDATAINDIA_DSN": dsn})


@pytest.mark.parametrize("drift", [None, "tag", "traffic", "image", "identity"])
def test_registration_requires_actual_serving_rolling_profile(monkeypatch, drift):
    monkeypatch.setattr("mfdataindia.review.image_provenance.verify_image", lambda *args: None)
    m = manifest()
    expected = revision_name(m, ROLLING_FACTORY)
    r = revision(m)
    r["spec"]["containers"][0]["args"] = api_args(ROLLING_FACTORY)
    if drift == "image":
        r["spec"]["containers"][0]["image"] = "wrong-image"
    if drift == "identity":
        r["spec"]["serviceAccountName"] = "wrong-identity"
    traffic = [{"revisionName": expected, "percent": 100}]
    if drift == "tag":
        traffic.append({"revisionName": expected, "tag": "retained"})
    if drift == "traffic":
        traffic[0]["revisionName"] = "different-serving-revision"

    class Boundary:
        inspections = 0

        def service(self):
            return {
                "metadata": {"annotations": {"run.googleapis.com/ingress": "internal"}},
                "status": {"url": "https://fixture.a.run.app", "traffic": traffic},
            }

        def revision(self, name):
            assert name == expected
            return deepcopy(r)

        def iam(self, complete):
            assert complete

        def inspect_execution(self, *args):
            self.inspections += 1
            return {"status": {"completionTime": "2026-01-01T00:00:00Z"}}, seal_evidence(
                m,
                "rolling-http",
                {
                    "level": "DEV_PRIVATE_HTTP",
                    "source_writes": 0,
                    "records_appended": 0,
                    "origin": "https://fixture.a.run.app",
                    "audience": "https://fixture.a.run.app",
                    "policy_hash": fingerprint(rolling.policy()),
                    "human_review_or_consumer_rbac_certified": False,
                    "cohort": [{"code": c} for c in m.cohort],
                    "coverage_observed": m.required_coverage,
                },
            )

    b = Boundary()
    if drift:
        with pytest.raises(ValueError):
            operations.verified_record(
                b,
                m,
                "fixture-execution",
                "https://fixture.a.run.app",
                {"mfdata_verified_reader": 1, "mfdata_verified_admin": 1},
            )
        assert b.inspections == 0
    else:
        record = operations.verified_record(
            b,
            m,
            "fixture-execution",
            "https://fixture.a.run.app",
            {"mfdata_verified_reader": 1, "mfdata_verified_admin": 1},
        )
        assert record["format"] == "rolling-analytics-certification-v1" and b.inspections == 1


def test_policy_bool_is_not_version_integer():
    value = rolling.policy()
    value["version"] = True
    with pytest.raises(ValueError):
        rolling.validate_policy(value)
