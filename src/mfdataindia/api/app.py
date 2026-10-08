"""FastAPI application exposing the MFDataIndia store.

Read-only. Serves JSON under ``/api`` and the static UI from ``mfdataindia/web``.
Each request checks out its own connection from a bounded psycopg pool so FastAPI's
threadpool can serve concurrent readers without sharing a connection across threads.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from starlette.middleware.base import BaseHTTPMiddleware

from mfdataindia.api import queries
from mfdataindia.store.dsn import resolve_dsn

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


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

    # -- JSON API ------------------------------------------------------------

    @app.get("/api/stats")
    def api_stats() -> dict[str, Any]:
        with pool.connection() as conn:
            return queries.stats(conn)

    @app.get("/api/funds")
    def api_funds(
        q: Optional[str] = Query(None),
        amc: Optional[str] = Query(None),
        category: Optional[str] = Query(None),
        option: Optional[str] = Query(None),
        in_scope: bool = Query(True),
        live: bool = Query(True),
        page: int = Query(1, ge=1),
        per_page: int = Query(50, ge=1, le=500),
        sort: str = Query("name"),
    ) -> dict[str, Any]:
        with pool.connection() as conn:
            return queries.list_funds(
                conn, q=q, amc=amc, category=category, option=option,
                in_scope=in_scope, live=live, page=page, per_page=per_page, sort=sort)

    @app.get("/api/fund-families")
    def api_fund_families(
        q: Optional[str] = Query(None),
        amc: Optional[str] = Query(None),
        category: Optional[str] = Query(None),
        option: Optional[str] = Query(None),
        live: bool = Query(True),
        page: int = Query(1, ge=1),
        per_page: int = Query(50, ge=1, le=500),
        sort: str = Query("name"),
    ) -> dict[str, Any]:
        with pool.connection() as conn:
            return queries.list_fund_families(
                conn, q=q, amc=amc, category=category, option=option,
                live=live, page=page, per_page=per_page, sort=sort)

    # NOTE: registered BEFORE /api/funds/{code} — "batch" is not an int, so the
    # {code} route would 422 this path first otherwise.
    @app.get("/api/funds/batch")
    def api_funds_batch(ids: str = Query(...)) -> dict[str, Any]:
        parts = [p.strip() for p in ids.split(",") if p.strip()]
        if not parts:
            raise HTTPException(
                status_code=422,
                detail="ids must be a comma-separated list of AMFI codes and/or ISINs")
        if len(parts) > 50:
            raise HTTPException(status_code=422, detail="at most 50 ids per request")
        with pool.connection() as conn:
            return queries.funds_batch(conn, parts)

    @app.get("/api/funds/{code}")
    def api_fund(code: int) -> dict[str, Any]:
        with pool.connection() as conn:
            fund = queries.fund_detail(conn, code)
        if fund is None:
            raise HTTPException(status_code=404, detail=f"fund {code} not found")
        return fund

    @app.get("/api/funds/{code}/nav")
    def api_fund_nav(
        code: int, years: Optional[float] = Query(None, ge=0)
    ) -> dict[str, Any]:
        with pool.connection() as conn:
            return queries.nav_series(conn, code, years=years)

    @app.get("/api/funds/{code}/returns")
    def api_fund_returns(code: int) -> dict[str, Any]:
        with pool.connection() as conn:
            return queries.returns(conn, code)

    @app.get("/api/funds/{code}/analytics")
    def api_fund_analytics(code: int) -> dict[str, Any]:
        with pool.connection() as conn:
            return queries.fund_analytics(conn, code)

    @app.get("/api/funds/{code}/peers")
    def api_fund_peers(code: int) -> dict[str, Any]:
        with pool.connection() as conn:
            return queries.fund_peers(conn, code)

    @app.get("/api/funds/{code}/risk-reward")
    def api_fund_risk_reward(code: int) -> dict[str, Any]:
        with pool.connection() as conn:
            return queries.risk_reward(conn, code)

    @app.get("/api/holdings-overlap")
    def api_holdings_overlap(codes: str = Query(...)) -> dict[str, Any]:
        parsed = [int(c) for c in codes.split(",") if c.strip().isdigit()][:6]
        if len(parsed) < 2:
            raise HTTPException(
                status_code=422, detail="codes must be 2+ comma-separated ints")
        with pool.connection() as conn:
            return queries.holdings_overlap(conn, parsed)

    @app.get("/api/amcs")
    def api_amcs() -> list[dict[str, Any]]:
        with pool.connection() as conn:
            return queries.amcs(conn)

    @app.get("/api/categories")
    def api_categories() -> list[dict[str, Any]]:
        with pool.connection() as conn:
            return queries.categories(conn)

    @app.get("/api/options")
    def api_options() -> list[str]:
        with pool.connection() as conn:
            return queries.options(conn)

    @app.get("/api/suggest")
    def api_suggest(q: str = Query(""), limit: int = Query(10, ge=1, le=25)) -> list[dict[str, Any]]:
        with pool.connection() as conn:
            return queries.suggest(conn, q, limit=limit)

    @app.get("/api/movers")
    def api_movers(
        period: str = Query("1m"),
        direction: str = Query("gainers"),
        limit: int = Query(10, ge=1, le=50),
    ) -> dict[str, Any]:
        with pool.connection() as conn:
            return queries.movers(conn, period=period, direction=direction, limit=limit)

    @app.get("/api/movers/categories")
    def api_movers_categories(
        period: str = Query("1m"),
        limit: int = Query(5, ge=1, le=20),
    ) -> dict[str, Any]:
        with pool.connection() as conn:
            return queries.category_movers(conn, period=period, limit=limit)

    @app.get("/api/compare")
    def api_compare(
        codes: str = Query(...),
        years: float = Query(1.0, ge=0),
    ) -> dict[str, Any]:
        parsed = [int(c) for c in codes.split(",") if c.strip().isdigit()][:4]
        if not parsed:
            raise HTTPException(status_code=422, detail="codes must be comma-separated ints")
        with pool.connection() as conn:
            return queries.compare(conn, parsed, years=years)

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        with pool.connection() as conn:
            v = conn.execute("SELECT version() AS v").fetchone()["v"]
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
