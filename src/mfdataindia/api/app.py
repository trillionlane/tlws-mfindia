"""FastAPI application exposing the MFDataIndia store.

Read-only. Serves JSON under ``/api`` and the static UI from ``mfdataindia/web``.
A connection pool is used because psycopg connections are not safe for the
concurrent threadpool that FastAPI sync endpoints run on.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from starlette.middleware.base import BaseHTTPMiddleware

from mfdataindia.api import queries
from mfdataindia.store.dsn import resolve_dsn

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

def create_app(dsn: Optional[str] = None) -> FastAPI:
    # No built-in default. This used to fall back to the PGlite dev instance on
    # 5433, which serves stale fund data while looking perfectly healthy.
    dsn = resolve_dsn(dsn, purpose="the MFDataIndia API")

    app = FastAPI(title="MFDataIndia", version="0.1.0")

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

    # The embedded engine (PGlite) is single-writer and serialises queries, and
    # its socket bridge is fragile when many client connections are held open —
    # a pool of held connections starves new ones. So the API uses ONE shared
    # connection guarded by a lock: all requests serialise through it, which
    # matches the engine's model exactly. For a durable multi-user deployment,
    # point MFDATAINDIA_DSN at real PostgreSQL and this stays correct (just less
    # concurrent than a pool).
    state: dict[str, Any] = {"conn": None}
    lock = threading.Lock()

    def get_conn():
        """The shared connection, reconnecting if it was dropped by the server.

        The engine may reap an idle connection; a server-side close is only
        discovered when the client next uses it, so we probe cheaply and reopen.
        """
        conn = state["conn"]
        if conn is not None and not conn.closed:
            try:
                conn.execute("SELECT 1").fetchone()
                return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                state["conn"] = None
                conn = None
        conn = psycopg.connect(
            dsn, connect_timeout=15, autocommit=True,
            row_factory=dict_row, prepare_threshold=None,
        )
        with conn.cursor() as cur:
            cur.execute("SET search_path TO mf, public")
        state["conn"] = conn
        return conn

    @app.on_event("shutdown")
    def _close() -> None:
        conn = state["conn"]
        if conn is not None and not conn.closed:
            conn.close()
        state["conn"] = None

    # -- JSON API ------------------------------------------------------------

    @app.get("/api/stats")
    def api_stats() -> dict[str, Any]:
        with lock:
            return queries.stats(get_conn())

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
        with lock:
            conn = get_conn()
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
        with lock:
            conn = get_conn()
            return queries.list_fund_families(
                conn, q=q, amc=amc, category=category, option=option,
                live=live, page=page, per_page=per_page, sort=sort)

    @app.get("/api/funds/{code}")
    def api_fund(code: int) -> dict[str, Any]:
        with lock:
            conn = get_conn()
            fund = queries.fund_detail(conn, code)
        if fund is None:
            raise HTTPException(status_code=404, detail=f"fund {code} not found")
        return fund

    @app.get("/api/funds/{code}/nav")
    def api_fund_nav(
        code: int, years: Optional[float] = Query(None, ge=0)
    ) -> dict[str, Any]:
        with lock:
            conn = get_conn()
            return queries.nav_series(conn, code, years=years)

    @app.get("/api/funds/{code}/returns")
    def api_fund_returns(code: int) -> dict[str, Any]:
        with lock:
            conn = get_conn()
            return queries.returns(conn, code)

    @app.get("/api/amcs")
    def api_amcs() -> list[dict[str, Any]]:
        with lock:
            conn = get_conn()
            return queries.amcs(conn)

    @app.get("/api/categories")
    def api_categories() -> list[dict[str, Any]]:
        with lock:
            conn = get_conn()
            return queries.categories(conn)

    @app.get("/api/options")
    def api_options() -> list[str]:
        with lock:
            conn = get_conn()
            return queries.options(conn)

    @app.get("/api/suggest")
    def api_suggest(q: str = Query(""), limit: int = Query(10, ge=1, le=25)) -> list[dict[str, Any]]:
        with lock:
            conn = get_conn()
            return queries.suggest(conn, q, limit=limit)

    @app.get("/api/movers")
    def api_movers(
        period: str = Query("1m"),
        direction: str = Query("gainers"),
        limit: int = Query(10, ge=1, le=50),
    ) -> dict[str, Any]:
        with lock:
            conn = get_conn()
            return queries.movers(conn, period=period, direction=direction, limit=limit)

    @app.get("/api/compare")
    def api_compare(
        codes: str = Query(...),
        years: float = Query(1.0, ge=0),
    ) -> dict[str, Any]:
        parsed = [int(c) for c in codes.split(",") if c.strip().isdigit()][:4]
        if not parsed:
            raise HTTPException(status_code=422, detail="codes must be comma-separated ints")
        with lock:
            conn = get_conn()
            return queries.compare(conn, parsed, years=years)

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        with lock:
            conn = get_conn()
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
