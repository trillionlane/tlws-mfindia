import json
import subprocess
import sys
import inspect
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import HTTPException, Response

from mfdataindia.api.association_app import create_association_app
from mfdataindia.api.association_schemas import AssociationTagMutation

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "mfdataindia-association-tags-v1.json"
RESOURCE = "/api/fund-families/{tlws_mf_id}/association-tags"


def test_committed_association_contract_is_current() -> None:
    subprocess.run(
        [sys.executable, "scripts/export_association_openapi.py", "--check"],
        cwd=ROOT,
        check=True,
    )
    contract = json.loads(CONTRACT.read_text())
    assert contract["info"]["x-contract-id"] == "mfdataindia-association-tags-v1"
    assert contract["info"]["x-contract-version"] == 1
    assert contract["x-mfdata-private"] is True
    assert contract["x-mfdata-browser-access"] is False
    assert contract["x-mfdata-authentication"] == "google-oidc-workload-identity"
    assert set(contract["paths"][RESOURCE]) == {"get", "post"}
    assert "securitySchemes" not in contract.get("components", {})


def test_write_contract_is_closed_and_bounded() -> None:
    contract = json.loads(CONTRACT.read_text())
    schemas = contract["components"]["schemas"]
    request = schemas["AssociationTagMutation"]
    assert request["additionalProperties"] is False
    assert request["properties"]["expected_version"]["minimum"] == 0
    tags = request["properties"]["tags"]
    assert tags["minItems"] == 1
    assert tags["maxItems"] == 50
    tag = schemas["AssociationTag"]
    assert tag["additionalProperties"] is False
    assert tag["properties"]["type"]["const"] == "scheme_alias"
    assert tag["properties"]["source"]["const"] == "trillion-insights"


def test_default_off_post_returns_before_database_checkout() -> None:
    app = create_association_app(
        "postgresql://unused:unused@127.0.0.1:1/unused",
        writes_enabled=False,
    )
    route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == RESOURCE and "POST" in getattr(route, "methods", set())
    )
    # Call the sync endpoint directly so the app lifespan never attempts the
    # deliberately invalid DSN. The feature gate must fire before pool access.
    request_model = AssociationTagMutation(
        expected_version=0,
        tags=[
            {
                "value": "Nippon India Taiwan Equity Fund",
                "type": "scheme_alias",
                "source": "trillion-insights",
            }
        ],
    )
    response = Response()
    with pytest.raises(HTTPException) as exc:
        inspect.unwrap(route.endpoint)(
            UUID("11111111-1111-1111-1111-111111111111"),
            request_model,
            response,
            "stable-key-001",
        )
    assert exc.value.status_code == 503
