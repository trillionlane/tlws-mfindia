"""FastAPI application exposing the MFDataIndia store.

Read-only. Serves JSON under ``/api`` and the static UI from ``mfdataindia/web``.
Each request checks out its own connection from a bounded psycopg pool so FastAPI's
threadpool can serve concurrent readers without sharing a connection across threads.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, AsyncIterator, Callable, Optional, TypeVar

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mfdataindia.api import queries, schemas
from mfdataindia.store.dsn import resolve_dsn

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_T = TypeVar("_T")


@dataclass
class _RequestMetrics:
    """Mutable request timings shared with FastAPI's worker thread."""

    pool_wait_ms: float = 0.0
    db_ms: float = 0.0


_REQUEST_METRICS: ContextVar[Optional[_RequestMetrics]] = ContextVar(
    "mfdataindia_request_metrics", default=None
)


def _emit_performance_event(message: str) -> None:
    """Write one JSON line for Cloud Run structured-log ingestion."""
    print(message, flush=True)


class _ApiTelemetryMiddleware:
    """Emit one low-cardinality performance event for every API response."""

    def __init__(self, app: ASGIApp, emit: Optional[Callable[[str], None]] = None) -> None:
        self.app = app
        self.emit = emit or _emit_performance_event

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        metrics = _RequestMetrics()
        token = _REQUEST_METRICS.set(metrics)
        status_code = 500
        response_bytes = 0
        completed = False

        async def send_with_metrics(message: Message) -> None:
            nonlocal status_code, response_bytes
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_with_metrics)
            completed = True
        finally:
            route = scope.get("route")
            route_template = getattr(route, "path", "unmatched")
            event = {
                "completed": completed,
                "db_ms": round(metrics.db_ms, 3),
                "duration_ms": round((perf_counter() - started) * 1000, 3),
                "event": "api_request_completed",
                "method": scope.get("method", "unknown"),
                "pool_wait_ms": round(metrics.pool_wait_ms, 3),
                "response_bytes": response_bytes,
                "route": route_template,
                "status_code": status_code,
            }
            try:
                self.emit(json.dumps(event, separators=(",", ":"), sort_keys=True))
            finally:
                _REQUEST_METRICS.reset(token)


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def _plan_scope_or_422(value: str) -> str:
    """Resolve a ``plan`` query parameter before the request touches the database.

    Validating up front makes a typo a 422 instead of a 500 from inside a query,
    and — more importantly — it can never be silently coerced into a wider or
    narrower universe than the caller asked for.
    """
    try:
        return queries.plan_scope(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def create_app(dsn: Optional[str] = None) -> FastAPI:
    # No built-in default. This used to fall back to the PGlite dev instance on
    # 5433, which serves stale fund data while looking perfectly healthy.
    dsn = resolve_dsn(dsn, purpose="the MFDataIndia API")

    pool_min = _positive_int("MFDATAINDIA_POOL_MIN_SIZE", 1)
    pool_max = _positive_int("MFDATAINDIA_POOL_MAX_SIZE", 10)
    if pool_min > pool_max:
        raise ValueError("MFDATAINDIA_POOL_MIN_SIZE cannot exceed MFDATAINDIA_POOL_MAX_SIZE")

    def _configure_connection(conn: Connection) -> None:
        conn.execute("SET search_path TO mf, public")

    pool = ConnectionPool(
        conninfo=dsn,
        min_size=pool_min,
        max_size=pool_max,
        timeout=15,
        open=False,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": None,
            "connect_timeout": 15,
        },
        configure=_configure_connection,
        name="mfdataindia-api",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool.open()
        try:
            pool.wait(timeout=15)
            app.state.db_pool = pool
            yield
        finally:
            pool.close()

    app = FastAPI(title="MFDataIndia", version="0.1.0", lifespan=lifespan)

    # The UI is iterated on constantly during ingest work. Starlette's
    # StaticFiles sends only ETag/Last-Modified, so browsers heuristic-cache
    # fund.js for a while and silently show the pre-change UI after a deploy.
    # no-cache keeps caching cheap (revalidate -> 304) but never stale.
    _UI_PATHS = ("/", "/compare")

    class _NoCacheUI(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            p = request.url.path
            if p.startswith("/static/") or p in _UI_PATHS or p.startswith("/fund/"):
                response.headers["Cache-Control"] = "no-cache"
            return response

    app.add_middleware(_NoCacheUI)

    # Compress JSON responses above a small threshold. Telemetry is added last,
    # making it the outer middleware so response_bytes is the actual compressed
    # transfer size when the client advertises gzip support.
    app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)
    app.add_middleware(_ApiTelemetryMiddleware)

    def run_db(callback: Callable[[Connection], _T]) -> _T:
        """Run one endpoint operation while separating pool wait from DB time."""
        metrics = _REQUEST_METRICS.get()
        wait_started = perf_counter()
        with pool.connection() as conn:
            query_started = perf_counter()
            if metrics is not None:
                metrics.pool_wait_ms += (query_started - wait_started) * 1000
            try:
                return callback(conn)
            finally:
                if metrics is not None:
                    metrics.db_ms += (perf_counter() - query_started) * 1000

    # -- JSON API ------------------------------------------------------------

    @app.get("/api/stats")
    def api_stats() -> dict[str, Any]:
        return run_db(queries.stats)

    @app.get("/api/funds")
    def api_funds(
        q: Optional[str] = Query(None, max_length=200),
        amc: Optional[str] = Query(None, max_length=200),
        category: Optional[str] = Query(None, max_length=200),
        option: Optional[str] = Query(None, max_length=100),
        in_scope: bool = Query(True),
        live: bool = Query(True),
        page: int = Query(1, ge=1, le=100_000),
        per_page: int = Query(50, ge=1, le=500),
        sort: str = Query("name", max_length=32),
        plan: str = Query(queries.DEFAULT_PLAN_SCOPE, max_length=16),
    ) -> dict[str, Any]:
        scope = _plan_scope_or_422(plan)
        return run_db(
            lambda conn: queries.list_funds(
                conn,
                q=q,
                amc=amc,
                category=category,
                option=option,
                in_scope=in_scope,
                live=live,
                page=page,
                per_page=per_page,
                sort=sort,
                plan=scope,
            )
        )

    @app.get("/api/fund-families")
    def api_fund_families(
        q: Optional[str] = Query(None, max_length=200),
        amc: Optional[str] = Query(None, max_length=200),
        category: Optional[str] = Query(None, max_length=200),
        option: Optional[str] = Query(None, max_length=100),
        live: bool = Query(True),
        page: int = Query(1, ge=1, le=100_000),
        per_page: int = Query(50, ge=1, le=500),
        sort: str = Query("name", max_length=32),
        plan: str = Query(queries.DEFAULT_PLAN_SCOPE, max_length=16),
    ) -> dict[str, Any]:
        scope = _plan_scope_or_422(plan)
        return run_db(
            lambda conn: queries.list_fund_families(
                conn,
                q=q,
                amc=amc,
                category=category,
                option=option,
                live=live,
                page=page,
                per_page=per_page,
                sort=sort,
                plan=scope,
            )
        )

    # NOTE: registered BEFORE /api/funds/{code} — "batch" is not an int, so the
    # {code} route would 422 this path first otherwise.
    @app.get("/api/funds/batch")
    def api_funds_batch(ids: str = Query(..., max_length=2048)) -> dict[str, Any]:
        parts = [p.strip() for p in ids.split(",") if p.strip()]
        if not parts:
            raise HTTPException(
                status_code=422,
                detail="ids must be a comma-separated list of AMFI codes and/or ISINs",
            )
        if len(parts) > 50:
            raise HTTPException(status_code=422, detail="at most 50 ids per request")
        return run_db(lambda conn: queries.funds_batch(conn, parts))

    @app.get(
        "/api/funds/{code}", response_model=schemas.FundDetail, response_model_exclude_unset=True
    )
    def api_fund(
        code: int, plan: str = Query(queries.DEFAULT_PLAN_SCOPE, max_length=16)
    ) -> dict[str, Any]:
        # ``plan`` scopes the sibling navigation list only: the requested code is
        # always resolvable by code, but the page must not offer a Direct hop.
        scope = _plan_scope_or_422(plan)
        fund = run_db(lambda conn: queries.fund_detail(conn, code, plan=scope))
        if fund is None:
            raise HTTPException(status_code=404, detail=f"fund {code} not found")
        return fund

    @app.get("/api/funds/{code}/nav")
    def api_fund_nav(
        code: int, years: Optional[float] = Query(None, ge=0, le=100)
    ) -> dict[str, Any]:
        return run_db(lambda conn: queries.nav_series(conn, code, years=years))

    @app.get(
        "/api/funds/{code}/returns",
        response_model=schemas.ReturnsResponse,
        response_model_exclude_unset=True,
    )
    def api_fund_returns(code: int) -> dict[str, Any]:
        return run_db(lambda conn: queries.returns(conn, code))

    @app.get(
        "/api/funds/{code}/analytics",
        response_model=schemas.AnalyticsResponse,
        response_model_exclude_unset=True,
    )
    def api_fund_analytics(code: int) -> dict[str, Any]:
        return run_db(lambda conn: queries.fund_analytics(conn, code))

    @app.get(
        "/api/funds/{code}/peers",
        response_model=schemas.PeersResponse,
        response_model_exclude_unset=True,
    )
    def api_fund_peers(code: int) -> dict[str, Any]:
        return run_db(lambda conn: queries.fund_peers(conn, code))

    @app.get(
        "/api/funds/{code}/risk-reward",
        response_model=schemas.RiskRewardResponse,
        response_model_exclude_unset=True,
    )
    def api_fund_risk_reward(code: int) -> dict[str, Any]:
        return run_db(lambda conn: queries.risk_reward(conn, code))

    @app.get("/api/holdings-overlap")
    def api_holdings_overlap(codes: str = Query(..., max_length=128)) -> dict[str, Any]:
        parsed = [int(c) for c in codes.split(",") if c.strip().isdigit()][:6]
        if len(parsed) < 2:
            raise HTTPException(status_code=422, detail="codes must be 2+ comma-separated ints")
        return run_db(lambda conn: queries.holdings_overlap(conn, parsed))

    @app.get("/api/amcs")
    def api_amcs() -> list[dict[str, Any]]:
        return run_db(queries.amcs)

    @app.get("/api/categories")
    def api_categories() -> list[dict[str, Any]]:
        return run_db(queries.categories)

    @app.get("/api/options")
    def api_options() -> list[str]:
        return run_db(queries.options)

    @app.get("/api/suggest")
    def api_suggest(
        q: str = Query("", max_length=200),
        limit: int = Query(10, ge=1, le=25),
        plan: str = Query(queries.DEFAULT_PLAN_SCOPE, max_length=16),
    ) -> list[dict[str, Any]]:
        scope = _plan_scope_or_422(plan)
        return run_db(lambda conn: queries.suggest(conn, q, limit=limit, plan=scope))

    @app.get(
        "/api/movers", response_model=schemas.MoversResponse, response_model_exclude_unset=True
    )
    def api_movers(
        period: str = Query("1m", max_length=16),
        direction: str = Query("gainers", max_length=16),
        limit: int = Query(10, ge=1, le=50),
        plan: str = Query(queries.DEFAULT_PLAN_SCOPE, max_length=16),
    ) -> dict[str, Any]:
        scope = _plan_scope_or_422(plan)
        return run_db(
            lambda conn: queries.movers(
                conn, period=period, direction=direction, limit=limit, plan=scope
            )
        )

    @app.get(
        "/api/movers/categories",
        response_model=schemas.CategoryMoversResponse,
        response_model_exclude_unset=True,
    )
    def api_movers_categories(
        period: str = Query("1m", max_length=16),
        limit: int = Query(5, ge=1, le=20),
        plan: str = Query(queries.DEFAULT_PLAN_SCOPE, max_length=16),
    ) -> dict[str, Any]:
        scope = _plan_scope_or_422(plan)
        return run_db(
            lambda conn: queries.category_movers(conn, period=period, limit=limit, plan=scope)
        )

    @app.get("/api/compare")
    def api_compare(
        codes: str = Query(..., max_length=128),
        years: float = Query(1.0, ge=0, le=100),
    ) -> dict[str, Any]:
        parsed = [int(c) for c in codes.split(",") if c.strip().isdigit()][:4]
        if not parsed:
            raise HTTPException(status_code=422, detail="codes must be comma-separated ints")
        return run_db(lambda conn: queries.compare(conn, parsed, years=years))

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        v = run_db(lambda conn: conn.execute("SELECT version() AS v").fetchone()["v"])
        return {"ok": True, "db": v}

    # -- static UI -----------------------------------------------------------

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(str(WEB_DIR / "index.html"))

        @app.get("/fund/{code}", include_in_schema=False)
        def fund_page(code: int) -> FileResponse:
            return FileResponse(str(WEB_DIR / "fund.html"))

        @app.get("/compare", include_in_schema=False)
        def compare_page() -> FileResponse:
            return FileResponse(str(WEB_DIR / "compare.html"))

    return app
