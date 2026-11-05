"""Dataset extension on the existing private core API and reader connection.

Authentication is the existing internal-ingress Google OIDC/Cloud Run invoker
boundary, NOT a newly trusted request header. TI retains its own partner and
backend authorization. No browser talks directly to MFData. This factory must
not be deployed with public/unauthenticated ingress; the existing release
preflight enforces that separate infrastructure boundary.
"""
from __future__ import annotations

import psycopg
from fastapi import Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from time import perf_counter

from mfdataindia.api.app import create_app as create_core_app, _REQUEST_METRICS

from . import catalogue, policy as p, reads, schemas


def create_app(dsn=None):
    app = create_core_app(dsn)
    errors = {status:{'model':schemas.DatasetErrorBody} for status in (404,409,422,503)}
    original_validation_handler = app.exception_handlers[RequestValidationError]

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        if request.url.path.startswith('/api/datasets/v1'):
            return JSONResponse(status_code=422,content={'detail':'INVALID_DATASET_QUERY'})
        return await original_validation_handler(request,error)

    def parameters(allowed):
        def check(request: Request):
            keys = [name for name,_ in request.query_params.multi_items()]
            if len(keys)!=len(set(keys)) or not set(keys)<=allowed:
                raise HTTPException(422,detail='INVALID_DATASET_QUERY')
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
        except p.DatasetError as error:
            reason = str(error)
            status = 404 if reason == 'DATASET_NOT_FOUND' else 409 if reason in {
                'STALE_OR_WRONG_DATASET_CURSOR', 'DATASET_RELATION_CHANGED'} else 422 if reason in {
                'INVALID_DATASET_CURSOR', 'INVALID_DATASET_LIMIT', 'INVALID_CONTENT_RANGE',
                'DUPLICATE_CURSOR_MEMBER'} else 503
            raise HTTPException(status, detail=reason) from None
        except (psycopg.errors.InvalidTextRepresentation, psycopg.errors.DatetimeFieldOverflow,
                psycopg.errors.NumericValueOutOfRange):
            raise HTTPException(422,detail='INVALID_DATASET_CURSOR') from None
        except (psycopg.Error, ValueError, TypeError):
            raise HTTPException(503, detail='DATASET_READ_UNAVAILABLE') from None

    @app.get('/api/datasets/v1', response_model=schemas.Discovery,
             responses=errors,
             dependencies=[Depends(parameters(set()))])
    def discovery():
        return run(catalogue.discovery)

    @app.get('/api/datasets/v1/{dataset_id}', response_model=schemas.Metadata,
             responses=errors,
             dependencies=[Depends(parameters(set()))])
    def metadata(dataset_id: str):
        return run(lambda conn: catalogue.metadata(conn, dataset_id))

    @app.get('/api/datasets/v1/{dataset_id}/rows', response_model=schemas.RowPage,
             responses=errors,
             dependencies=[Depends(parameters({'limit','cursor'}))])
    def rows(dataset_id: str, limit: int = Query(50, ge=1, le=p.MAX_PAGE),
             cursor: str | None = Query(None, min_length=1, max_length=p.MAX_CURSOR_BYTES * 2)):
        return run(lambda conn: reads.rows(conn, dataset_id, limit=limit, cursor=cursor))

    @app.get('/api/datasets/v1/{dataset_id}/content/{column}', response_model=schemas.ContentChunk,
             responses=errors,
             dependencies=[Depends(parameters({'row','offset','length'}))])
    def content(dataset_id: str, column: str,
                row: str = Query(min_length=1, max_length=p.MAX_CURSOR_BYTES * 2),
                offset: int = Query(0, ge=0, lt=2**31),
                length: int = Query(p.MAX_BINARY_CHUNK, ge=1, le=p.MAX_BINARY_CHUNK)):
        return run(lambda conn: reads.content(conn, dataset_id, column, row=row, offset=offset, length=length))

    return app
