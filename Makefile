# Common tasks. Override PYTHON / OSQUERYD / MONGOD as needed (see scripts/stack.sh).
PYTHON ?= .venv/bin/python

.PHONY: install seed up down status logs test test-integration lint web-install web-dev web-build web-test

install:              ## Python venv + package with dev extras
	python3 -m venv .venv && $(PYTHON) -m pip install -U pip && $(PYTHON) -m pip install -e ".[dev]"

seed:                 ## 30 days of synthetic snapshots (needs MongoDB running)
	$(PYTHON) -m showquery.seed --days 30 --reset

up:                   ## mongod -> osqueryd -> extension -> API on :8000
	scripts/stack.sh up

down:
	scripts/stack.sh down

status:
	scripts/stack.sh status

logs:
	scripts/stack.sh logs

test:                 ## unit tests (no services needed)
	$(PYTHON) -m pytest -q

test-integration:     ## pushdown oracle tests against the running stack
	SHOWQUERY_OSQUERY_SOCKET=$${SHOWQUERY_RUN_DIR:-$(CURDIR)/.run}/osquery.sock $(PYTHON) -m pytest -q -m integration

lint:
	$(PYTHON) -m ruff check src tests

web-install:
	cd web && npm install

web-dev:              ## Vite dev server on :5173, proxies /api to :8000
	cd web && npm run dev

web-build:            ## builds web/dist, which the API then serves on :8000
	cd web && npm run build

web-test:
	cd web && npm test
