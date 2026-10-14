"""Private opt-in enhancement API, isolated from the existing 19 read endpoints."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated, Callable

import jwt
import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import ValidationError

from mfdataindia.calculators import schemas as calculator_schemas
from mfdataindia.calculators import historical_schemas
from mfdataindia.review import publication, schemas, store
from mfdataindia.review.auth import AssertionVerifier, Principal
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.store.dsn import resolve_dsn
from mfdataindia.store.locks import DataOperationBusy

Hash = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]
Code = Annotated[int, Path(gt=0, le=2147483647)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


def create_review_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None, research: bool = False, calendar: bool = False, rolling: bool = False, calculators: bool = False, historical: bool = False, enrichment: bool = False) -> FastAPI:
    # One optional mounted trust file, not one flag/environment variable per gate.
    trust_file = os.environ.get("MFDATAINDIA_REVIEW_TRUST_FILE")
    if verifier is None and trust_file:
        verifier = AssertionVerifier.from_file(trust_file)
    pool = ConnectionPool(conninfo=resolve_dsn(dsn, purpose="verified fund review API"),
                          min_size=1, max_size=5, timeout=10, open=False,
                          kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": 10})

    @asynccontextmanager
    async def lifespan(app):
        # No configured verifier: dormant app does not connect even on startup.
        if verifier is not None:
            pool.open()
            try:
                pool.wait(timeout=10)
                with pool.connection() as conn:
                    verify_runtime(conn, "api")
                    if research:
                        publication.verify_installation(conn)
                    if calendar:
                        from mfdataindia.analytics.store import verify_acl
                        verify_acl(conn)
                    if rolling:
                        from mfdataindia.analytics.rolling_store import verify_acl as verify_rolling_acl
                        verify_rolling_acl(conn)
                    if calculators:
                        from mfdataindia.calculators.store import installed_policy
                        installed_policy(conn)
                    if historical:
                        from mfdataindia.calculators.historical_store import installed_policy as installed_historical_policy
                        installed_historical_policy(conn)
                    if enrichment:
                        from mfdataindia.enrichment.store import verify_acl as verify_enrichment_acl
                        verify_enrichment_acl(conn)
                yield
            finally:
                pool.close()
        else:
            yield

    app = FastAPI(title="MFData Verified Fund Review", version="1.0.0", lifespan=lifespan)
    bearer = HTTPBearer(auto_error=False)

    def identity(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
        if verifier is None:
            raise HTTPException(503, "review trust is not configured")
        if credentials is None:
            raise HTTPException(401, "signed backend assertion required", headers={"WWW-Authenticate": "Bearer"})
        try:
            principal = verifier.verify(credentials.credentials)
        except (jwt.PyJWTError, ValueError, ValidationError):
            raise HTTPException(401, "invalid backend assertion") from None
        if not principal.scopes & {"review:read", "review:admin"}:
            raise HTTPException(403, "review access required")
        return principal

    def administrator(principal: Principal = Depends(identity)) -> Principal:
        if "review:admin" not in principal.scopes:
            raise HTTPException(403, "admin assertion required")
        return principal

    @app.middleware("http")
    async def no_store(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def run(callback: Callable, *, write: bool = False):
        try:
            with pool.connection() as conn, conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL " + ("READ COMMITTED" if write else "REPEATABLE READ, READ ONLY"))
                conn.execute("SET LOCAL statement_timeout='10s'")
                return callback(conn)
        except store.NotFound:
            raise HTTPException(404, "fund, policy or result not found") from None
        except (store.Conflict, DataOperationBusy, psycopg.errors.UniqueViolation, psycopg.errors.SerializationFailure, psycopg.errors.RaiseException):
            raise HTTPException(409, "inputs/version changed or operation busy; refresh before retry") from None
        except psycopg.errors.InsufficientPrivilege:
            raise HTTPException(503, "review runtime privileges not ready") from None
        except ValueError:
            raise HTTPException(422, "unsupported or oversized source input") from None

    @app.get("/api/review/v1/policies/{policy_hash}", response_model=schemas.PolicyResponse)
    def get_policy(policy_hash: Hash, principal: Principal = Depends(identity)):
        def read(conn):
            row = conn.execute("SELECT policy_hash,policy_id,policy_version,document FROM mf_review.policies WHERE policy_hash=%s", (policy_hash,)).fetchone()
            if not row:
                raise store.NotFound()
            return row
        return run(read)

    @app.get("/api/review/v1/funds", response_model=schemas.QueueResponse)
    def list_queue(principal: Principal = Depends(identity), after_code: int = Query(0,ge=0),
                   limit: int = Query(50,ge=1,le=100), status: schemas.Status | None = None,
                   signal: Annotated[str | None, Query(pattern=r"^(constant_nav_series|duplicate_variant_series|terminal_face_value_reset_candidate)$")] = None):
        return run(lambda conn: store.queue(conn, after_code=after_code, limit=limit, status=status, signal=signal))

    @app.get("/api/review/v1/funds/{code}/analytics", response_model=schemas.ResultResponse)
    def get_analytics(code: Code, principal: Principal = Depends(identity),
                      result_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None):
        return run(lambda conn: store.get_result(conn, code, result_id))

    @app.get("/api/review/v1/funds/{code}", response_model=schemas.ReviewResponse)
    def detail(code: Code, principal: Principal = Depends(identity),
               after_version: int = Query(0,ge=0), limit: int = Query(50,ge=1,le=100)):
        return run(lambda conn: store.get_review(conn, code, after_version=after_version, limit=limit))

    @app.post("/api/review/v1/funds/{code}/reviews", response_model=schemas.EventResponse)
    def review(code: Code, request: schemas.ReviewMutation, idempotency_key: IdempotencyKey,
               principal: Principal = Depends(administrator)):
        return run(lambda conn: store.record_event(conn, code, request.model_dump(mode="json"), kind="review",
                   caller=principal.caller, actor=principal.actor, idempotency_key=idempotency_key), write=True)

    @app.post("/api/review/v1/funds/{code}/enrichment-proposals", response_model=schemas.EventResponse)
    def proposal(code: Code, request: schemas.ProposalMutation, idempotency_key: IdempotencyKey,
                 principal: Principal = Depends(administrator)):
        return run(lambda conn: store.record_event(conn, code, request.model_dump(mode="json"), kind="proposal",
                   caller=principal.caller, actor=principal.actor, idempotency_key=idempotency_key), write=True)

    if research:
        @app.get('/api/review/v1/funds/{code}/publication', response_model=schemas.PublicationResponse)
        def research_publication(code: Code, principal: Principal = Depends(identity)):
            return run(lambda conn: publication.get_publication(conn, code))

    if calendar:
        from mfdataindia.analytics import schemas as calendar_schemas, store as calendar_store

        @app.get('/api/research/v1/funds/{code}/calendar-returns', response_model=calendar_schemas.Response | calendar_schemas.AuditResponse)
        def calendar_returns(code: Code, principal: Principal = Depends(identity),
                             result_id: Annotated[str | None, Query(pattern=r'^[0-9a-f]{64}$')] = None):
            if result_id and 'review:admin' not in principal.scopes:
                raise HTTPException(403, 'admin assertion required for retained audit artifacts')
            return run(lambda conn: calendar_store.get_calendar(conn,code,result_id))

    if rolling:
        from mfdataindia.analytics import rolling_schemas, rolling_store

        @app.get('/api/research/v1/funds/{code}/rolling-returns', response_model=rolling_schemas.PagedResponse | rolling_schemas.AuditResponse)
        def rolling_returns(code: Code, principal: Principal = Depends(identity),
                            years: int = Query(1,ge=1,le=5,json_schema_extra={'enum':[1,3,5]}), limit: int = Query(100,ge=1,le=100),
                            cursor: Annotated[str | None, Query(max_length=512)] = None,
                            result_id: Annotated[str | None, Query(pattern=r'^[0-9a-f]{64}$')] = None):
            if result_id and 'review:admin' not in principal.scopes:
                raise HTTPException(403, 'admin assertion required for retained audit artifacts')
            if result_id and cursor:
                raise HTTPException(422, 'retained audit and normal cursor cannot be combined')
            return run(lambda conn: rolling_store.get_rolling(conn,code,result_id) if result_id else
                       rolling_store.page(rolling_store.get_rolling(conn,code),years=years,limit=limit,cursor=cursor))

    if calculators:
        from mfdataindia.calculators import forward as forward_calculator
        from mfdataindia.calculators.store import installed_policy

        @app.post('/api/calculators/v1/projections', response_model=calculator_schemas.Response)
        def forward_projection(request: calculator_schemas.Request, principal: Principal = Depends(identity)):
            def calculate(conn):
                policy=installed_policy(conn)
                try:
                    return forward_calculator.derive(request.model_dump(mode='json'),policy)
                except forward_calculator.CalculationUnavailable as exc:
                    raise HTTPException(422,{'code':'CALCULATION_UNAVAILABLE','reason':str(exc)}) from None
            return run(calculate)

    if historical:
        from mfdataindia.calculators import historical as historical_engine
        from mfdataindia.calculators.historical_store import installed_policy as installed_historical_policy

        @app.post('/api/calculators/v1/backtests', response_model=historical_schemas.Response)
        def historical_backtest(request: historical_schemas.Request, principal: Principal = Depends(identity)):
            def calculate(conn):
                document = installed_historical_policy(conn)
                snapshot = store.capture(conn, request.code)
                return historical_engine.derive(request.model_dump(mode='json'), snapshot, document)
            return run(calculate)

    if enrichment:
        from mfdataindia.enrichment import evidence, schemas as enrichment_schemas, store as enrichment_store
        from mfdataindia.review.policy import fingerprint

        @app.get('/api/enrichment/v1/policy', response_model=enrichment_schemas.PolicyResponse)
        def enrichment_policy(principal: Principal = Depends(identity)):
            return run(lambda conn: {'policy_hash': fingerprint(evidence.policy()), 'document': enrichment_store.installed_policy(conn)})

        @app.get('/api/enrichment/v1/documents', response_model=enrichment_schemas.DocumentPage)
        def enrichment_documents(principal: Principal = Depends(identity),
                                 after: Annotated[str, Query(pattern=r'^(?:[0-9a-f]{64})?$')] = '',
                                 limit: int = Query(50,ge=1,le=100)):
            return run(lambda conn: enrichment_store.documents(conn,after=after,limit=limit))

        @app.get('/api/enrichment/v1/documents/{document_id}', response_model=enrichment_schemas.Document)
        def enrichment_document(document_id: Hash, principal: Principal = Depends(identity)):
            return run(lambda conn: enrichment_store.get_document(conn,document_id))

        @app.get('/api/enrichment/v1/funds/{code}/coverage', response_model=enrichment_schemas.Coverage)
        def enrichment_coverage(code: Code, principal: Principal = Depends(identity),
                                document_id: Annotated[str | None, Query(pattern=r'^[0-9a-f]{64}$')] = None):
            return run(lambda conn: enrichment_store.coverage(conn,code,document_id=document_id))

    return app


def create_research_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """Explicit deployment profile; unchanged review-v1 factory keeps six routes."""
    return create_review_app(dsn, verifier=verifier, research=True)


def create_calendar_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """Additive eight-route profile; old factories/contracts stay unchanged."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True)


def create_rolling_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """Additive nine-route profile; all earlier factories/contracts unchanged."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True)


def create_calculator_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """Additive ten-route profile; computations are private and non-persisting."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True)


def create_historical_calculator_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """Eleven-route profile; earlier factories and contracts stay frozen."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True,historical=True)


def create_enrichment_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """Fifteen-route opt-in profile; no public intake or original-document download."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True,historical=True,enrichment=True)
