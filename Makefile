# MFDataIndia — common tasks. The local stack uses embedded PostgreSQL (PGlite);
# use `docker compose up` + MFDATAINDIA_DSN for a durable/real deployment.

SHELL := /bin/bash
PY    := PYTHONPATH=src python3

.PHONY: help install up api bootstrap backfill test clean db-stop

help:
	@echo "make install      — install Python deps"
	@echo "make up           — start local stack (embedded Postgres + API + UI)"
	@echo "make api          — start only the API"
	@echo "make bootstrap    — load NAVAll + 1y NAV history + bundled Scripbox evidence"
	@echo "make backfill YEARS=5 — backfill the full 5-year NAV window (resumable)"
	@echo "make test         — unit tests (PostgreSQL integration needs MF_TEST_DSN)"
	@echo "make db-stop      — stop the embedded database"

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
	MF_TEST_NO_COPY=1 $(PY) scripts/bootstrap_local.py --years 5 --min-delay 1.0

test:
	$(PY) -m pytest tests/ -q

db-stop:
	@[ -f data/pglite/server.pid ] && kill "$$(cat data/pglite/server.pid)" && echo "stopped" || echo "not running"

clean:
	rm -rf data/pglite .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
