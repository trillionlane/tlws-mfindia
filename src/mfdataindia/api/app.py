"""FastAPI application exposing the MFDataIndia store.

Read-only. Serves JSON under ``/api`` and the static UI from ``mfdataindia/web``.
A connection pool is used because psycopg connections are not safe for the
concurrent threadpool that FastAPI sync endpoints run on.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from mfdataindia.api import queries

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

DEFAULT_DSN = "host=127.0.0.1 port=5433 user=postgres dbname=postgres sslmode=disable"


def create_app(dsn: Optional[str] = None) -> FastAPI:
    dsn = dsn or os.environ.get("MFDATAINDIA_DSN") or DEFAULT_DSN

    app = FastAPI(title="MFDataIndia", version="0.1.0")

    def _configure(conn) -> None:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO mf, public")

    pool = ConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=4,
        kwargs={"row_factory": dict_row, "autocommit": True, "prepare_threshold": None},
        configure=_configure,
        open=False,
    )

    @app.on_event("startup")
    def _open() -> None:
        pool.open()

    @app.on_event("shutdown")
    def _close() -> None:
        pool.close()

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

    return app
