"""Trusted private core/dataset composition plus bounded semantic tag GETs.

Like the existing dataset factory, this uses internal ingress and authenticated
Cloud Run invokers, not request-controlled identity/profile headers. TI keeps
its own partner authorization. It never installs or publishes on startup.
"""
from __future__ import annotations

from time import perf_counter
from typing import Literal

import psycopg
from fastapi import Depends, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from mfdataindia.datasets.app import create_app as dataset_app
from mfdataindia.datasets.policy import DatasetError
from mfdataindia.api.app import _REQUEST_METRICS
from . import policy as p, reads, schemas as s


def create_app(dsn=None):
    app = dataset_app(dsn)
    previous_validation = app.exception_handlers[RequestValidationError]

    @app.exception_handler(RequestValidationError)
    async def validation(request, error):
        if request.url.path.startswith(reads.PREFIX):
            return JSONResponse(status_code=422,content={'detail':'INVALID_TAG_QUERY'})
        return await previous_validation(request,error)

    def parameters(allowed, repeated=()):
        def check(request: Request):
            keys = [name for name,_ in request.query_params.multi_items()]
            if not set(keys) <= allowed or any(keys.count(k)>1 for k in keys if k not in repeated):
                raise HTTPException(422,detail='INVALID_TAG_QUERY')
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
        except p.TaggingError as error:
            reason = str(error)
            status = 422 if reason in {'INVALID_TAG_QUERY','INVALID_TAG_CURSOR'} else 409 if reason == 'TAG_SNAPSHOT_CHANGED' else 503
            # Only our explicit stable labels are consumer-visible.
            allowed = {'INVALID_TAG_QUERY','INVALID_TAG_CURSOR','TAG_SNAPSHOT_CHANGED',
                       'TAGGING_NOT_PUBLISHED','TAGGING_READ_MODEL_DIFFERS','TAG_RESPONSE_TOO_LARGE'}
            raise HTTPException(status,detail=reason if reason in allowed else 'TAGGING_READ_UNAVAILABLE') from None
        except DatasetError as error:
            reason = str(error)
            allowed = {'DATASET_NOT_FOUND','DATASET_READ_GRANTS_MISSING','UNSAFE_DATASET_CAPABILITY',
                       'DATASET_READER_IDENTITY_UNSAFE','DATASET_READ_PROFILE_UNSAFE',
                       'SENSITIVE_DATASET_VALUE','DATASET_RELATION_CHANGED'}
            raise HTTPException(503,detail=reason if reason in allowed else 'TAGGING_READ_UNAVAILABLE') from None
        except (psycopg.Error, ValueError, TypeError):
            raise HTTPException(503,detail='TAGGING_READ_UNAVAILABLE') from None

    errors = {code:{'model':s.TagErrorBody} for code in (409,422,503)}

    @app.get(reads.PREFIX,response_model=s.TagDiscovery,responses=errors,
             dependencies=[Depends(parameters(set()))])
    def discovery():
        return run(reads.discovery)

    @app.get(reads.PREFIX+'/definitions',response_model=s.TagDefinitionPage,responses=errors,
             dependencies=[Depends(parameters({'dimension','limit','cursor'}))])
    def definitions(dimension: s.Dimension | None = None, limit: int = Query(50,ge=1,le=100),
                    cursor: str | None = Query(None,min_length=1,max_length=2048)):
        if dimension is not None and dimension not in p.DIMENSIONS:
            raise HTTPException(422,detail='INVALID_TAG_QUERY')
        return run(lambda conn: reads.definitions(conn,dimension=dimension,limit=limit,cursor=cursor))

    @app.get(reads.PREFIX+'/batch',response_model=s.TaggedFundBatch,responses=errors,
             dependencies=[Depends(parameters({'codes'}))])
    def batch(codes: str = Query(min_length=1,max_length=550)):
        atoms = codes.split(',')
        if not (1 <= len(atoms) <= 50 and all(a.isascii() and a.isdecimal() and a == str(int(a)) for a in atoms)):
            raise HTTPException(422,detail='INVALID_TAG_QUERY')
        parsed = [int(a) for a in atoms]
        try:
            reads.validate_codes(parsed)
        except p.TaggingError:
            raise HTTPException(422,detail='INVALID_TAG_QUERY') from None
        return run(lambda conn: reads.batch(conn,parsed))

    @app.get(reads.PREFIX+'/funds',response_model=s.TaggedFundPage,responses=errors,
             dependencies=[Depends(parameters({'tag','match','limit','cursor'},repeated={'tag'}))])
    def funds(tag: list[str] = Query(default=[]), match: Literal['all','any'] = 'all',
              limit: int = Query(20,ge=1,le=50), cursor: str | None = Query(None,min_length=1,max_length=2048)):
        try:
            reads.validate_filters(tag,match,limit)
        except p.TaggingError:
            raise HTTPException(422,detail='INVALID_TAG_QUERY') from None
        return run(lambda conn: reads.funds(conn,tags=tag,match=match,limit=limit,cursor=cursor))

    @app.get(reads.PREFIX+'/funds/{code}',response_model=s.TaggedFundBatch,responses=errors,
             dependencies=[Depends(parameters(set()))])
    def fund(code: int = Path(gt=0,lt=2**31)):
        return run(lambda conn: reads.batch(conn,[code]))

    return app
