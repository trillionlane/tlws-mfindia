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
from mfdataindia.enrichment import fact_review_schemas
from mfdataindia.review import publication, schemas, store
from mfdataindia.review.auth import AssertionVerifier, Principal
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.store.dsn import resolve_dsn
from mfdataindia.store.locks import DataOperationBusy

Hash = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]
Code = Annotated[int, Path(gt=0, le=2147483647)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


def create_review_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None, research: bool = False, calendar: bool = False, rolling: bool = False, calculators: bool = False, historical: bool = False, enrichment: bool = False, official_facts: bool = False, portfolios: bool = False, overlap: bool = False, distributions: bool = False, idcw_catalog: bool = False, current_facts: bool = False, monthly_facts: bool = False, fact_refresh: bool = False, fact_verification: bool = False, amc_coverage: bool = False, multi_amc: bool = False) -> FastAPI:
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
                    if official_facts:
                        from mfdataindia.enrichment.facts_store import verify_acl as verify_facts_acl
                        verify_facts_acl(conn)
                    if portfolios:
                        from mfdataindia.enrichment.portfolio_store import verify_acl as verify_portfolio_acl
                        verify_portfolio_acl(conn)
                    if overlap:
                        from mfdataindia.analytics.overlap_store import verify_acl as verify_overlap_acl
                        verify_overlap_acl(conn)
                    if distributions:
                        from mfdataindia.enrichment.distribution_store import verify_acl as verify_distribution_acl
                        verify_distribution_acl(conn)
                    if idcw_catalog:
                        from mfdataindia.enrichment.idcw_store import verify_acl as verify_idcw_acl
                        verify_idcw_acl(conn)
                    if current_facts:
                        from mfdataindia.enrichment.current_facts_store import verify_acl as verify_current_facts_acl
                        verify_current_facts_acl(conn)
                    if monthly_facts:
                        from mfdataindia.enrichment.monthly_store import verify_acl as verify_monthly_acl
                        verify_monthly_acl(conn)
                    if fact_refresh:
                        from mfdataindia.enrichment.refresh_store import verify_acl as verify_refresh_acl
                        verify_refresh_acl(conn)
                    if fact_verification:
                        from mfdataindia.enrichment.fact_review_store import verify_acl as verify_fact_review_acl
                        verify_fact_review_acl(conn)
                    if amc_coverage:
                        from mfdataindia.enrichment.amc_register import load_register
                        load_register()
                    if multi_amc:
                        from mfdataindia.enrichment.multi_store import verify_acl as verify_multi_acl
                        verify_multi_acl(conn)
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

    if official_facts:
        from mfdataindia.enrichment import facts, facts_schemas, facts_store
        from mfdataindia.review.policy import fingerprint

        @app.get('/api/enrichment/v2/policy', response_model=facts_schemas.PolicyResponse)
        def official_facts_policy(principal: Principal = Depends(identity)):
            return run(lambda conn: {'policy_hash': fingerprint(facts.policy()), 'document': facts_store.installed_policy(conn)})

        @app.get('/api/enrichment/v2/funds/{code}/facts', response_model=facts_schemas.Response)
        def official_facts_read(code: Code, principal: Principal = Depends(identity),
                                snapshot_id: Annotated[str | None, Query(pattern=r'^[0-9a-f]{64}$')] = None):
            return run(lambda conn: facts_store.get(conn,code,snapshot_id=snapshot_id))

        @app.get('/api/enrichment/v2/funds/{code}/snapshots', response_model=facts_schemas.Page)
        def official_facts_snapshots(code: Code, principal: Principal = Depends(identity),
                                     after: Annotated[str, Query(pattern=r'^(?:[0-9a-f]{64})?$')] = '',
                                     limit: int = Query(50,ge=1,le=100)):
            return run(lambda conn: facts_store.snapshots(conn,code,after=after,limit=limit))

    if portfolios:
        from mfdataindia.enrichment import portfolio_schemas, portfolio_store, portfolios as portfolio_engine
        from mfdataindia.review.policy import fingerprint

        @app.get('/api/portfolios/v1/policy', response_model=portfolio_schemas.PolicyResponse)
        def portfolio_policy(principal: Principal = Depends(identity)):
            return run(lambda conn: {'policy_hash': fingerprint(portfolio_engine.policy()), 'document': portfolio_store.installed_policy(conn)})

        @app.get('/api/portfolios/v1/funds/{code}/portfolio', response_model=portfolio_schemas.Response)
        def portfolio_read(code: Code, principal: Principal = Depends(identity),
                           snapshot_id: Annotated[str | None, Query(pattern=r'^[0-9a-f]{64}$')] = None):
            return run(lambda conn: portfolio_store.get(conn,code,snapshot_id=snapshot_id)[0])

        @app.get('/api/portfolios/v1/funds/{code}/snapshots', response_model=portfolio_schemas.SnapshotPage)
        def portfolio_snapshots(code: Code, principal: Principal = Depends(identity),
                                after: Annotated[str, Query(pattern=r'^(?:[0-9a-f]{64})?$')] = '',
                                limit: Annotated[int, Query(ge=1,le=100)] = 50):
            return run(lambda conn: portfolio_store.snapshots(conn,code,after=after,limit=limit))

        @app.get('/api/portfolios/v1/funds/{code}/snapshots/{snapshot_id}/holdings', response_model=portfolio_schemas.HoldingsPage)
        def portfolio_holdings(code: Code, snapshot_id: Hash, principal: Principal = Depends(identity),
                               after_row: Annotated[int, Query(ge=0,le=1000)] = 0,
                               limit: Annotated[int, Query(ge=1,le=100)] = 50):
            return run(lambda conn: portfolio_store.holdings(conn,code,snapshot_id,after=after_row,limit=limit))

    if overlap:
        from mfdataindia.analytics import overlap as overlap_engine, overlap_schemas, overlap_store
        from mfdataindia.enrichment.portfolio_schemas import PolicyResponse
        from mfdataindia.review.policy import fingerprint

        @app.get('/api/overlap/v1/policy', response_model=PolicyResponse)
        def overlap_policy(principal: Principal = Depends(identity)):
            return run(lambda conn: {'policy_hash': fingerprint(overlap_engine.policy()), 'document': overlap_store.installed_policy(conn)})

        @app.get('/api/overlap/v1/pairs/{left_code}/{right_code}', response_model=overlap_schemas.Response)
        def overlap_pair(left_code: Code, right_code: Code, principal: Principal = Depends(identity),
                         left_snapshot_id: Annotated[str | None, Query(pattern=r'^[0-9a-f]{64}$')] = None,
                         right_snapshot_id: Annotated[str | None, Query(pattern=r'^[0-9a-f]{64}$')] = None):
            return run(lambda conn: overlap_store.get(conn,left_code,right_code,left_snapshot_id=left_snapshot_id,right_snapshot_id=right_snapshot_id)[0])

        @app.get('/api/overlap/v1/results/{result_id}', response_model=overlap_schemas.Result)
        def overlap_result(result_id: Hash, principal: Principal = Depends(identity)):
            return run(lambda conn: {k:v for k,v in overlap_store.result(conn,result_id).items() if k!='shared_holdings'})

        @app.get('/api/overlap/v1/results/{result_id}/shared-holdings', response_model=overlap_schemas.Page)
        def overlap_shared(result_id: Hash, principal: Principal = Depends(identity),
                           after_isin: Annotated[str, Query(pattern=r'^(?:[A-Z]{2}[A-Z0-9]{9}[0-9])?$')] = '',
                           limit: Annotated[int, Query(ge=1,le=100)] = 50):
            return run(lambda conn: overlap_store.shared(conn,result_id,after=after_isin,limit=limit))

    if distributions:
        from mfdataindia.enrichment import distributions as distribution_engine, distribution_schemas, distribution_store
        from mfdataindia.enrichment.portfolio_schemas import PolicyResponse
        from mfdataindia.review.policy import fingerprint

        @app.get('/api/distributions/v1/policy', response_model=PolicyResponse)
        def distribution_policy(principal: Principal = Depends(identity)):
            return run(lambda conn: {'policy_hash': fingerprint(distribution_engine.policy()), 'document': distribution_store.installed_policy(conn)})

        @app.get('/api/distributions/v1/funds/{code}/coverage', response_model=distribution_schemas.DistributionCoverage)
        def distribution_coverage(code: Code, principal: Principal = Depends(identity)):
            return run(lambda conn: distribution_store.coverage(conn, code))

        @app.get('/api/distributions/v1/funds/{code}/observations', response_model=distribution_schemas.DistributionResponse)
        def distribution_observations(code: Code, snapshot_id: Annotated[str, Query(pattern=r'^[0-9a-f]{64}$')], principal: Principal = Depends(identity)):
            return run(lambda conn: distribution_store.get(conn, code, snapshot_id))

    if idcw_catalog:
        from mfdataindia.enrichment import idcw_catalog as catalog, idcw_store
        from mfdataindia.enrichment.portfolio_schemas import PolicyResponse
        from mfdataindia.review.policy import fingerprint

        @app.get('/api/distributions/v2/policy', response_model=PolicyResponse)
        def idcw_policy(principal: Principal = Depends(identity)):
            return run(lambda conn: {'policy_hash': fingerprint(catalog.policy()), 'document': idcw_store.installed_policy(conn)})

        @app.get('/api/distributions/v2/inventory', response_model=catalog.IdcwInventory)
        def idcw_inventory(principal: Principal = Depends(identity), after_code: int = Query(0, ge=0, le=2147483647), limit: int = Query(50, ge=1, le=100)):
            return run(lambda conn: idcw_store.inventory(conn, after=after_code, limit=limit))

        @app.get('/api/distributions/v2/funds/{code}/coverage', response_model=catalog.IdcwCoverage)
        def idcw_coverage(code: Code, principal: Principal = Depends(identity)):
            return run(lambda conn: idcw_store.coverage(conn, code))

        @app.get('/api/distributions/v2/funds/{code}/observations', response_model=catalog.IdcwResponse)
        def idcw_observations(code: Code, snapshot_id: Annotated[str, Query(pattern=r'^[0-9a-f]{64}$')], principal: Principal = Depends(identity)):
            return run(lambda conn: idcw_store.get(conn, code, snapshot_id))

    if current_facts:
        from mfdataindia.enrichment import current_facts as current, current_facts_store
        from mfdataindia.review.policy import fingerprint

        @app.get('/api/enrichment/v3/policy', response_model=current.CurrentPolicyResponse)
        def current_facts_policy(principal: Principal = Depends(identity)):
            def selected(conn):
                document=current_facts_store.policy_document(conn)
                return {'policy_hash':fingerprint(document),'document':document}
            return run(selected)

        @app.get('/api/enrichment/v3/funds/{code}/facts', response_model=current.CurrentFactsResponse)
        def current_official_facts(code: Code, principal: Principal = Depends(identity)):
            return run(lambda conn: current_facts_store.get(conn, code))

    if monthly_facts:
        from mfdataindia.enrichment import monthly_facts as monthly, monthly_schemas, monthly_store
        from mfdataindia.review.policy import fingerprint

        @app.get('/api/enrichment/v4/policy', response_model=monthly_schemas.MonthlyPolicyResponse)
        def monthly_reporting_policy(principal: Principal = Depends(identity)):
            return run(lambda conn: {'policy_hash':fingerprint(monthly.policy()),'document':monthly.policy()})

        @app.get('/api/enrichment/v4/funds/{code}/facts', response_model=monthly_schemas.MonthlyResponse)
        def monthly_reported_facts(code: Code, snapshot_id: Annotated[str | None, Query(pattern=r'^[0-9a-f]{64}$')] = None, principal: Principal = Depends(identity)):
            return run(lambda conn: monthly_store.get(conn,code,snapshot_id=snapshot_id))

    if fact_refresh:
        from mfdataindia.enrichment import refresh_store, refresh_policy, refresh_schemas
        from mfdataindia.review.policy import fingerprint

        @app.get('/api/enrichment/v5/policy', response_model=refresh_schemas.RefreshPolicyResponse)
        def refresh_policy_document(principal: Principal = Depends(identity)):
            return run(lambda conn: {'policy_hash':fingerprint(refresh_policy.policy()), 'document':refresh_policy.policy()})

        @app.get('/api/enrichment/v5/status', response_model=refresh_schemas.RefreshStatus)
        def refresh_status(principal: Principal = Depends(identity)):
            return run(refresh_store.status)

    if fact_verification:
        from mfdataindia.enrichment import fact_review_store as fact_reviews

        @app.get('/api/enrichment/v6/funds/{code}/history', response_model=fact_review_schemas.FactSourceHistory)
        def fact_source_history(code: Code, principal: Principal = Depends(identity),
            after_id: Annotated[str | None, Query(pattern=r'^[0-9a-f]{64}$')] = None,
            limit: int = Query(50,ge=1,le=100)):
            return run(lambda conn: fact_reviews.history(conn,code,after_id=after_id,limit=limit))

        @app.get('/api/enrichment/v6/funds/{code}/changes', response_model=fact_review_schemas.FactSourceChanges)
        def fact_source_changes(code: Code,from_snapshot_id: Annotated[str, Query(pattern=r'^[0-9a-f]{64}$')],
            to_snapshot_id: Annotated[str, Query(pattern=r'^[0-9a-f]{64}$')],principal: Principal = Depends(identity)):
            return run(lambda conn: fact_reviews.changes(conn,code,from_snapshot_id,to_snapshot_id))

        @app.get('/api/enrichment/v6/funds/{code}/verification', response_model=fact_review_schemas.FactReviewState)
        def fact_verification_state(code: Code,principal: Principal = Depends(identity),
            after_version: int = Query(0,ge=0),limit:int=Query(50,ge=1,le=100)):
            return run(lambda conn: fact_reviews.state(conn,code,after_version=after_version,limit=limit))

        @app.post('/api/enrichment/v6/funds/{code}/reviews', response_model=fact_review_schemas.FactReviewEvent)
        def review_official_facts(code: Code,request: fact_review_schemas.FactReviewMutation,
            idempotency_key: IdempotencyKey,principal: Principal = Depends(administrator)):
            return run(lambda conn: fact_reviews.record(conn,code,request.model_dump(mode='json'),
                caller=principal.caller,actor=principal.actor,idempotency_key=idempotency_key),write=True)

    if amc_coverage:
        from mfdataindia.enrichment import amc_coverage as census, amc_register, amc_coverage_schemas
        from mfdataindia.review.policy import fingerprint

        @app.get('/api/enrichment/v7/policy', response_model=amc_coverage_schemas.CoveragePolicy)
        def coverage_policy(principal: Principal = Depends(identity)):
            return {'policy_hash':fingerprint(amc_register.policy()), 'document':amc_register.policy()}

        @app.get('/api/enrichment/v7/coverage', response_model=amc_coverage_schemas.AmcCoverageResponse)
        def amc_census(principal: Principal = Depends(identity)):
            return run(census.summary)

        @app.get('/api/enrichment/v7/amcs/{amc_key}/schemes', response_model=amc_coverage_schemas.SchemePage)
        def amc_schemes(amc_key: Annotated[str, Path(pattern=r'^(amfi|db)-[1-9][0-9]{0,9}$')],
            principal: Principal = Depends(identity), after_code: int = Query(0,ge=0,le=2147483647),
            limit: int = Query(50,ge=1,le=100),
            expected_revision: Annotated[str | None, Query(pattern=r'^[0-9a-f]{64}$')] = None):
            return run(lambda conn:census.schemes(conn,amc_key,after_code=after_code,limit=limit,expected_revision=expected_revision))

    if multi_amc:
        from mfdataindia.enrichment import multi_facts, multi_store, multi_schemas
        from mfdataindia.review.policy import fingerprint

        @app.get('/api/enrichment/v8/policy', response_model=multi_schemas.RegisteredPolicy)
        def registered_source_policy(principal: Principal = Depends(identity)):
            return {'policy_hash':fingerprint(multi_facts.policy()),'document':multi_facts.policy()}

        @app.get('/api/enrichment/v8/refresh/status', response_model=multi_schemas.RegisteredStatus)
        def registered_source_status(principal: Principal = Depends(identity)):
            return run(multi_store.status)

        @app.get('/api/enrichment/v8/funds/{code}/facts', response_model=multi_schemas.RegisteredFacts)
        def registered_source_facts(code: Code,principal: Principal = Depends(identity)):
            return run(lambda conn:multi_store.get(conn,code))

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


def create_official_facts_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """Eighteen-route private disclosure profile; every earlier profile is frozen."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True,historical=True,enrichment=True,official_facts=True)


def create_portfolios_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """Explicit additive 22-route profile; prior 18-route factory is frozen."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True,historical=True,enrichment=True,official_facts=True,portfolios=True)


def create_overlap_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """Explicit additive 26-route profile; prior 22-route factory stays frozen."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True,historical=True,enrichment=True,official_facts=True,portfolios=True,overlap=True)


def create_distribution_observations_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """29-route private profile; reported evidence only, all prior contracts frozen."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True,historical=True,enrichment=True,official_facts=True,portfolios=True,overlap=True,distributions=True)


def create_idcw_catalog_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """33-route profile; v1 evidence and all prior contracts remain frozen."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True,historical=True,enrichment=True,official_facts=True,portfolios=True,overlap=True,distributions=True,idcw_catalog=True)


def create_current_facts_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """35-route profile; all 33 prior route/component contracts stay unchanged."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True,historical=True,enrichment=True,official_facts=True,portfolios=True,overlap=True,distributions=True,idcw_catalog=True,current_facts=True)


def create_monthly_facts_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """37-route private profile; official reported facts are not canonical analytics."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True,historical=True,enrichment=True,official_facts=True,portfolios=True,overlap=True,distributions=True,idcw_catalog=True,current_facts=True,monthly_facts=True)


def create_fact_refresh_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """39-route private profile; current provenance and bounded refresh status."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True,historical=True,enrichment=True,official_facts=True,portfolios=True,overlap=True,distributions=True,idcw_catalog=True,current_facts=True,monthly_facts=True,fact_refresh=True)


def create_fact_verification_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """43-route private profile; human decisions never edit source facts."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True,historical=True,enrichment=True,official_facts=True,portfolios=True,overlap=True,distributions=True,idcw_catalog=True,current_facts=True,monthly_facts=True,fact_refresh=True,fact_verification=True)


def create_amc_coverage_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """46-route profile: all-AMC census, unchanged acquisition and earlier APIs."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True,historical=True,enrichment=True,official_facts=True,portfolios=True,overlap=True,distributions=True,idcw_catalog=True,current_facts=True,monthly_facts=True,fact_refresh=True,fact_verification=True,amc_coverage=True)


def create_multi_amc_app(dsn: str | None = None, *, verifier: AssertionVerifier | None = None):
    """49 paths; explicit registry-driven source metadata profile."""
    return create_review_app(dsn,verifier=verifier,research=True,calendar=True,rolling=True,calculators=True,historical=True,enrichment=True,official_facts=True,portfolios=True,overlap=True,distributions=True,idcw_catalog=True,current_facts=True,monthly_facts=True,fact_refresh=True,fact_verification=True,amc_coverage=True,multi_amc=True)
