"""Trusted private read-only composition of tags plus dated exposures.

Internal ingress, workload authentication and TI partner authorization remain
mandatory. No request supplies an entity registry, identity, SQL or profile.
"""
from time import perf_counter

from fastapi import Depends, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import psycopg

from mfdataindia.api.app import _REQUEST_METRICS
from mfdataindia.datasets.policy import DatasetError
from . import app as tags, exposure_reads as reads, exposure_schemas as s
from .policy import TaggingError


def create_app(dsn=None):
    app = tags.create_app(dsn)
    previous = app.exception_handlers[RequestValidationError]

    @app.exception_handler(RequestValidationError)
    async def validation(request,error):
        if request.url.path.startswith(reads.PREFIX):
            return JSONResponse(status_code=422,content={'detail':'INVALID_EXPOSURE_QUERY'})
        return await previous(request,error)

    def parameters(allowed):
        def check(request: Request):
            keys = [name for name,_ in request.query_params.multi_items()]
            if len(keys)!=len(set(keys)) or not set(keys)<=allowed:
                raise HTTPException(422,detail='INVALID_EXPOSURE_QUERY')
        return check

    def run(callback):
        metrics = _REQUEST_METRICS.get()
        waiting = perf_counter()
        try:
            with app.state.db_pool.connection() as conn:
                started = perf_counter()
                if metrics is not None:
                    metrics.pool_wait_ms += (started-waiting)*1000
                try:
                    with conn.transaction():
                        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                        conn.execute("SET LOCAL statement_timeout='10s'; SET LOCAL lock_timeout='5s'; SET LOCAL TIME ZONE 'UTC'; SET LOCAL DateStyle='ISO,YMD'")
                        return callback(conn)
                finally:
                    if metrics is not None:
                        metrics.db_ms += (perf_counter()-started)*1000
        except TaggingError as error:
            reason = str(error)
            allowed = {'INVALID_EXPOSURE_QUERY','EXPOSURE_FUND_NOT_FOUND','EXPOSURE_ASSESSMENT_CHANGED',
                'EXPOSURE_READ_MODEL_DIFFERS','EXPOSURE_PORTFOLIO_DIFFERS','EXPOSURE_REGISTRY_DIFFERS',
                'EXPOSURE_EVIDENCE_DIFFERS','EXPOSURE_WEIGHT_DIFFERS','EXPOSURE_SIGNED_SECURITY_UNSUPPORTED',
                'TAG_RESPONSE_TOO_LARGE'}
            status = 422 if reason=='INVALID_EXPOSURE_QUERY' else 404 if reason=='EXPOSURE_FUND_NOT_FOUND' else 409 if reason=='EXPOSURE_ASSESSMENT_CHANGED' else 503
            raise HTTPException(status,detail=reason if reason in allowed else 'EXPOSURE_READ_UNAVAILABLE') from None
        except DatasetError as error:
            reason = str(error)
            allowed = {'DATASET_NOT_FOUND','DATASET_READ_GRANTS_MISSING','UNSAFE_DATASET_CAPABILITY',
                'DATASET_READER_IDENTITY_UNSAFE','DATASET_READ_PROFILE_UNSAFE',
                'DATASET_INSTALLED_READER_PROFILE_UNSAFE','DATASET_RELATION_CHANGED'}
            raise HTTPException(503,detail=reason if reason in allowed else 'EXPOSURE_READ_UNAVAILABLE') from None
        except (psycopg.Error,ValueError,TypeError):
            raise HTTPException(503,detail='EXPOSURE_READ_UNAVAILABLE') from None

    errors = {status:{'model':s.ExposureError} for status in (404,409,422,503)}

    @app.get(reads.PREFIX,response_model=s.ExposureDiscovery,responses=errors,
             dependencies=[Depends(parameters(set()))])
    def discovery():
        return run(reads.discovery)

    @app.get(reads.PREFIX+'/funds/{code}',response_model=s.ExposureSummary,responses=errors,
             dependencies=[Depends(parameters(set()))])
    def summary(code: int = Path(gt=0,lt=2**31)):
        return run(lambda conn:reads.summary(conn,code))

    @app.get(reads.PREFIX+'/funds/{code}/{section}',response_model=s.ExposurePage,responses=errors,
             dependencies=[Depends(parameters({'limit','assessment_id','after'}))])
    def page(section: s.Section, code: int = Path(gt=0,lt=2**31),
             limit: int = Query(50,ge=1,le=50),
             assessment_id: str | None = Query(None,pattern=r'^[0-9a-f]{64}$'),
             after: str | None = Query(None,pattern=r'^[0-9a-f]{64}$')):
        return run(lambda conn:reads.page(conn,code,section,limit=limit,assessment_id=assessment_id,after=after))

    return app
