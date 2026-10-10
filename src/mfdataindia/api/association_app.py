"""Private, default-off FastAPI app for TrillionInsights association writes."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from mfdataindia.api import association_tags
from mfdataindia.api.association_schemas import (
    AssociationConflict,
    AssociationTagMutation,
    AssociationTagMutationResponse,
    AssociationTagState,
)
from mfdataindia.store.dsn import resolve_dsn


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def create_association_app(
    dsn: str | None = None,
    *,
    writes_enabled: bool | None = None,
) -> FastAPI:
    dsn = resolve_dsn(dsn, purpose="the MFDataIndia association writer")
    enabled = (
        _enabled(os.environ.get("MFDATAINDIA_ASSOCIATION_WRITES_ENABLED"))
        if writes_enabled is None
        else writes_enabled
    )

    def configure(conn: Connection) -> None:
        conn.execute("SET search_path TO mf, public")

    pool = ConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=5,
        timeout=15,
        open=False,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": None,
            "connect_timeout": 15,
        },
        configure=configure,
        name="mfdataindia-association-writer",
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

    app = FastAPI(
        title="MFDataIndia Association Tags",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/api/health")
    def health() -> dict[str, object]:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"ok": True, "writes_enabled": enabled}

    @app.get(
        "/api/fund-families/{tlws_mf_id}/association-tags",
        response_model=AssociationTagState,
    )
    def get_association_tags(tlws_mf_id: UUID) -> dict:
        family_id = str(tlws_mf_id)
        try:
            with pool.connection() as conn:
                return association_tags.get_state(conn, family_id)
        except association_tags.FamilyNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"fund family {family_id} not found") from exc

    @app.post(
        "/api/fund-families/{tlws_mf_id}/association-tags",
        response_model=AssociationTagMutationResponse,
        responses={
            409: {"model": AssociationConflict},
            503: {"description": "Association writes are disabled"},
        },
    )
    def post_association_tags(
        tlws_mf_id: UUID,
        request: AssociationTagMutation,
        response: Response,
        idempotency_key: str = Header(
            ...,
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ) -> dict | JSONResponse:
        # This check deliberately happens before pool checkout: default-off is
        # a provable no-database-call/no-mutation state.
        if not enabled:
            raise HTTPException(status_code=503, detail="association writes are disabled")
        family_id = str(tlws_mf_id)
        try:
            with pool.connection() as conn:
                body, status = association_tags.add_tags(
                    conn,
                    family_id,
                    request,
                    idempotency_key=idempotency_key,
                )
            response.status_code = status
            return body
        except association_tags.FamilyNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"fund family {family_id} not found") from exc
        except association_tags.VersionConflictError as exc:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "expected_version does not match current state",
                    "current_version": exc.current_version,
                },
            )
        except association_tags.IdempotencyConflictError:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Idempotency-Key was already used for a different request",
                    "current_version": None,
                },
            )

    return app
