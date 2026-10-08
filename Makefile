# MFDataIndia — common tasks.
# PostgreSQL is the system of record: docker compose runs it on :5432.
# All data targets honor $MFDATAINDIA_DSN (set it to your own Postgres, or use
# the docker-compose instance). Real Postgres uses COPY bulk load.

SHELL := /bin/bash
PY    := PYTHONPATH=src python3
YEARS ?= 1
# Indian market data: "today" always means today in IST.
TODAY := $(shell TZ=Asia/Kolkata date +%F)

.PHONY: help install up api migrate bootstrap backfill nav-today compute enrich-factsheet build-family status test clean

help:
	@echo "make install           — install Python deps"
	@echo "make up                — start local stack (docker compose Postgres + API + UI)"
	@echo "make api               — start only the API (assumes Postgres already up)"
	@echo "make migrate           — apply pending checksummed forward migrations"
	@echo "make bootstrap YEARS=1 — load NAVAll + NAV window + bundled evidence"
	@echo "make backfill          — full 5-year NAV window (resumable)"
	@echo "make nav-today         — fetch today's NAV (IST; run after ~18:30 IST)"
	@echo "make compute           — fill NULL sharpe/vol/return fields from our NAV series"
	@echo "make enrich-factsheet  — fill NULL beta/Sharpe/std-dev from AMC monthly factsheets"
	@echo "make build-family      — build mf.fund_family (own tlws_mf_id / slug / tags)"
	@echo "make status            — coverage + backfill + enrichment progress"
	@echo "make test              — unit tests (integration needs MF_TEST_DSN)"
	@echo "make clean             — remove caches and stop docker compose"

install:
	python3 -m pip install -e .[dev]

up:
	./scripts/up.sh

api:
	./scripts/up.sh --api-only

migrate:
	$(PY) scripts/migrate.py

bootstrap:
	$(PY) scripts/bootstrap_local.py --years $(YEARS)

backfill:
	$(PY) scripts/backfill_nav.py --years 5 --min-delay 0.8

nav-today:
	$(PY) scripts/backfill_nav.py --from-date $(TODAY) --to-date $(TODAY) --min-delay 1.0

compute:
	$(PY) scripts/compute_metrics.py

enrich-factsheet:
	$(PY) scripts/enrich_amc_factsheets.py

build-family:
	$(PY) scripts/build_fund_family.py

status:
	$(PY) scripts/status.py

test:
	$(PY) -m pytest tests/ -q

clean:
	rm -rf .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	docker compose down
