"""API envelope tests that do not require PostgreSQL."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

from mfdataindia.api.app import _ApiTelemetryMiddleware, create_app


class _ListLogger:
    def __init__(self) -> None:
        self.events: list[str] = []

    def info(self, message: str) -> None:
        self.events.append(message)


def _performance_app(logger: _ListLogger) -> FastAPI:
    app = FastAPI()

    @app.get("/api/items/{item_id}")
    def item(item_id: int) -> JSONResponse:
        return JSONResponse({"item_id": item_id, "payload": "x" * 5_000})

    @app.get("/")
    def root() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)
    app.add_middleware(_ApiTelemetryMiddleware, emit=logger.info)
    return app


def test_large_api_responses_are_compressed_and_logged_by_route_template() -> None:
    logger = _ListLogger()

    with TestClient(_performance_app(logger)) as client:
        response = client.get("/api/items/123", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in response.headers["vary"]
    assert int(response.headers["content-length"]) < 500

    event = json.loads(logger.events[0])
    assert event["event"] == "api_request_completed"
    assert event["completed"] is True
    assert event["method"] == "GET"
    assert event["route"] == "/api/items/{item_id}"
    assert event["status_code"] == 200
    assert event["response_bytes"] == int(response.headers["content-length"])
    assert event["duration_ms"] >= 0
    assert event["pool_wait_ms"] == 0
    assert event["db_ms"] == 0


def test_non_api_routes_do_not_emit_api_performance_events() -> None:
    logger = _ListLogger()

    with TestClient(_performance_app(logger)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert logger.events == []


def test_unmatched_api_path_does_not_log_the_concrete_path() -> None:
    logger = _ListLogger()

    with TestClient(_performance_app(logger)) as client:
        response = client.get("/api/private-looking-value")

    assert response.status_code == 404
    event = json.loads(logger.events[0])
    assert event["route"] == "unmatched"
    assert "private-looking-value" not in logger.events[0]


def _query_parameter(app: FastAPI, path: str, name: str) -> dict:
    operation = app.openapi()["paths"][path]["get"]
    return next(parameter for parameter in operation["parameters"] if parameter["name"] == name)


def _value_schema(parameter: dict) -> dict:
    schema = parameter["schema"]
    return next((item for item in schema.get("anyOf", []) if item.get("type") != "null"), schema)


def test_expensive_query_inputs_have_openapi_upper_bounds() -> None:
    # Building the app and its OpenAPI document does not open the connection pool.
    app = create_app("postgresql://unused:unused@127.0.0.1:1/unused")

    assert _value_schema(_query_parameter(app, "/api/funds", "page"))["maximum"] == 100_000
    assert _value_schema(_query_parameter(app, "/api/funds", "q"))["maxLength"] == 200
    assert _value_schema(_query_parameter(app, "/api/funds/batch", "ids"))["maxLength"] == 2048
    assert _value_schema(_query_parameter(app, "/api/funds/{code}/nav", "years"))["maximum"] == 100
    assert _value_schema(_query_parameter(app, "/api/compare", "codes"))["maxLength"] == 128
