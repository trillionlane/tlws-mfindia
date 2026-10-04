# MFDataIndia — common tasks. The local stack uses embedded PostgreSQL (PGlite);
# use `docker compose up` + MFDATAINDIA_DSN for a durable/real deployment.

SHELL := /bin/bash
PY    := PYTHONPATH=src python3

.PHONY: help install up api bootstrap backfill enrich-scripbox enrich-groww status test clean db-stop

help:
	@echo "make install           — install Python deps"
	@echo "make up                — start local stack (embedded Postgres + API + UI)"
	@echo "make api               — start only the API"
	@echo "make bootstrap YEARS=1 — load NAVAll + NAV window + bundled evidence"
	@echo "make backfill          — full 5-year NAV window (resumable)"
	@echo "make enrich-scripbox   — Scripbox facts crawl (reliable core, resumable)"
	@echo "make enrich-groww      — Groww gaps backfill (run after Scripbox)"
	@echo "make enrich-groww-all  — Groww for ALL funds (benchmark, manager, expense history)"
	@echo "make status            — coverage + backfill + enrichment progress"
	@echo "make test              — unit tests (integration needs MF_TEST_DSN)"
	@echo "make db-stop           — stop the embedded database (clean flush)"

install:
	python3 -m pip install -e .[dev]

up:
	./scripts/up.sh

api:
	./scripts/up.sh --api-only

bootstrap:
	MF_TEST_NO_COPY=1 $(PY) scripts/bootstrap_local.py --years $(YEARS)

YEARS ?= 1

backfill:
	MF_TEST_NO_COPY=1 $(PY) scripts/backfill_nav.py --years 5 --min-delay 0.8

enrich-scripbox:
	MF_TEST_NO_COPY=1 $(PY) scripts/enrich_scripbox.py

enrich-groww:
	MF_TEST_NO_COPY=1 $(PY) scripts/enrich_groww.py

enrich-groww-all:
	MF_TEST_NO_COPY=1 $(PY) scripts/enrich_groww.py --all

status:
	MF_TEST_NO_COPY=1 $(PY) scripts/status.py

test:
	$(PY) -m pytest tests/ -q

db-stop:
	@PID=$$(lsof -nP -iTCP:$${MF_PG_PORT:-5433} -sTCP:LISTEN -t 2>/dev/null | head -1); \
	 [ -n "$$PID" ] && kill $$PID && echo "stopped (clean flush)" || echo "not running"

clean:
	rm -rf data/pglite .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
