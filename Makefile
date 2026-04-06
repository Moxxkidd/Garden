PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
UVICORN ?= $(if $(wildcard .venv/bin/uvicorn),.venv/bin/uvicorn,uvicorn)
APP_HOST ?= 127.0.0.1
APP_PORT ?= 8000

.PHONY: install dev test lint format run demo cli-health

install:
	$(PYTHON) -m pip install -e ".[dev]"

dev:
	$(UVICORN) app.main:app --reload --host $(APP_HOST) --port $(APP_PORT)

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

run:
	$(UVICORN) app.main:app --host $(APP_HOST) --port $(APP_PORT)

demo: run

cli-health:
	$(PYTHON) -m app.cli.main healthcheck
