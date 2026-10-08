from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "smoke_private_api", ROOT / "scripts" / "smoke_private_api.py"
)
assert SPEC and SPEC.loader
smoke_private_api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke_private_api)


class Response:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class Session:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> Response:
        self.requests.append((url, kwargs))
        return self.responses.pop(0)


def successful_responses() -> list[Response]:
    return [
        Response(200, text="short-lived-token"),
        Response(200, {"ok": True, "db": "PostgreSQL 18.6"}),
        Response(
            200,
            {
                "schemes_total": 14369,
                "in_scope_live": 4291,
                "nav_rows": 4219608,
                "nav_first": "2008-10-02",
                "nav_last": "2026-10-07",
                "dataset_version": 1,
            },
        ),
        Response(200, {"results": [{"amfi_scheme_code": 100033}]}),
        Response(
            200,
            {"amfi_scheme_code": 100033, "family": {"tlws_mf_id": "family-id"}},
        ),
        Response(200, {"code": 100033, "points": [{"date": "2026-10-07", "nav": 1}]}),
    ]


def test_private_smoke_uses_metadata_oidc_and_bounded_reads() -> None:
    session = Session(successful_responses())

    smoke_private_api.run_smoke(
        "https://mfdata.example.run.app",
        "https://mfdata.example.run.app",
        session,
    )

    assert len(session.requests) == 6
    metadata_url, metadata_request = session.requests[0]
    assert metadata_url == smoke_private_api.METADATA_IDENTITY_URL
    assert metadata_request["params"] == {"audience": "https://mfdata.example.run.app"}
    assert metadata_request["headers"] == {"Metadata-Flavor": "Google"}
    assert metadata_request["allow_redirects"] is False
    for url, request in session.requests[1:]:
        assert url.startswith("https://mfdata.example.run.app/api/")
        assert request["headers"]["Authorization"] == "Bearer short-lived-token"
        assert request["timeout"] == smoke_private_api.REQUEST_TIMEOUT_SECONDS
        assert request["allow_redirects"] is False


@pytest.mark.parametrize(
    ("base_url", "audience"),
    [
        ("http://mfdata.example.run.app", "http://mfdata.example.run.app"),
        ("https://user@mfdata.example.run.app", "https://user@mfdata.example.run.app"),
        ("https://mfdata.example.run.app/path", "https://mfdata.example.run.app/path"),
        ("https://mfdata.example.run.app", "https://other.example.run.app"),
    ],
)
def test_private_smoke_rejects_invalid_origin_or_audience(base_url: str, audience: str) -> None:
    with pytest.raises(smoke_private_api.PrivateSmokeError):
        smoke_private_api.run_smoke(base_url, audience, Session([]))


def test_private_smoke_does_not_echo_response_body_on_http_failure() -> None:
    session = Session(
        [
            Response(200, text="short-lived-token"),
            Response(403, text="sensitive upstream response"),
        ]
    )

    with pytest.raises(smoke_private_api.PrivateSmokeError) as error:
        smoke_private_api.run_smoke(
            "https://mfdata.example.run.app",
            "https://mfdata.example.run.app",
            session,
        )

    assert "HTTP 403" in str(error.value)
    assert "sensitive upstream response" not in str(error.value)


def test_private_smoke_rejects_invalid_json_without_echoing_content() -> None:
    session = Session(
        [
            Response(200, text="short-lived-token"),
            Response(200, ValueError("secret response content")),
        ]
    )

    with pytest.raises(smoke_private_api.PrivateSmokeError) as error:
        smoke_private_api.run_smoke(
            "https://mfdata.example.run.app",
            "https://mfdata.example.run.app",
            session,
        )

    assert "invalid JSON" in str(error.value)
    assert "secret response content" not in str(error.value)
