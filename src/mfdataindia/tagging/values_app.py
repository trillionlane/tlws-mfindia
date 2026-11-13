"""Trusted read-only composition for explicit custom sustainability screens."""
from time import perf_counter

from fastapi import Depends, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import psycopg

from mfdataindia.api.app import _REQUEST_METRICS
from mfdataindia.datasets.policy import DatasetError
from . import exposure_app, values_policy as policy, values_reads as reads, values_schemas as schemas


def create_app(dsn=None):
    app = exposure_app.create_app(dsn)
    previous = app.exception_handlers[RequestValidationError]

    @app.exception_handler(RequestValidationError)
    async def validation(request, error):
        if request.url.path.startswith(reads.PREFIX):
            return JSONResponse(status_code=422, content={'detail': 'INVALID_VALUES_QUERY'})
        return await previous(request, error)

    def parameters(request: Request):
        if request.query_params:
            raise HTTPException(422, detail='INVALID_VALUES_QUERY')

    def run(callback):
        metrics = _REQUEST_METRICS.get()
        waiting = perf_counter()
        try:
            with app.state.db_pool.connection() as conn:
                started = perf_counter()
                if metrics is not None:
                    metrics.pool_wait_ms += (started - waiting) * 1000
                try:
                    with conn.transaction():
                        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                        conn.execute("SET LOCAL statement_timeout='10s'; SET LOCAL lock_timeout='5s'; "
                                     "SET LOCAL TIME ZONE 'UTC'; SET LOCAL DateStyle='ISO,YMD'")
                        return callback(conn)
                finally:
                    if metrics is not None:
                        metrics.db_ms += (perf_counter() - started) * 1000
        except policy.TaggingError as error:
            reason = str(error)
            allowed = {
                'INVALID_VALUES_QUERY', 'EXPOSURE_FUND_NOT_FOUND', 'VALUES_EXPOSURE_DIFFERS',
                'VALUES_SCREEN_REGISTRY_DIFFERS', 'VALUES_ACTIVITY_REGISTRY_DIFFERS',
                'VALUES_EVIDENCE_DIFFERS', 'EXPOSURE_READ_MODEL_DIFFERS',
            }
            status = 422 if reason == 'INVALID_VALUES_QUERY' else 404 if reason == 'EXPOSURE_FUND_NOT_FOUND' else 503
            raise HTTPException(status, detail=reason if reason in allowed else 'VALUES_READ_UNAVAILABLE') from None
        except DatasetError as error:
            reason = str(error)
            allowed = {
                'DATASET_NOT_FOUND', 'DATASET_READ_GRANTS_MISSING', 'UNSAFE_DATASET_CAPABILITY',
                'DATASET_READER_IDENTITY_UNSAFE', 'DATASET_READ_PROFILE_UNSAFE',
                'DATASET_INSTALLED_READER_PROFILE_UNSAFE', 'DATASET_RELATION_CHANGED',
            }
            raise HTTPException(503, detail=reason if reason in allowed else 'VALUES_READ_UNAVAILABLE') from None
        except (psycopg.Error, ValueError, TypeError):
            raise HTTPException(503, detail='VALUES_READ_UNAVAILABLE') from None

    errors = {status: {'model': schemas.ValuesError} for status in (404, 422, 503)}

    @app.get(reads.PREFIX, response_model=schemas.ValuesDiscovery, responses=errors,
             dependencies=[Depends(parameters)])
    def discovery():
        return run(reads.discovery)

    @app.get(reads.PREFIX + '/funds/{code}', response_model=schemas.ValuesSummary,
             responses=errors, dependencies=[Depends(parameters)])
    def summary(code: int = Path(gt=0, lt=2**31)):
        return run(lambda conn: reads.summary(conn, code))

    return app
