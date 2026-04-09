# strm2stl — common dev commands
# Run from strm2stl/ (the directory containing this Makefile)

VENV := ../.venv
PYTHON := $(VENV)/Scripts/python
PIP := $(VENV)/Scripts/pip
PYTEST := $(VENV)/Scripts/pytest
RUFF := $(VENV)/Scripts/ruff

# Fallback to system Python if venv not found
ifeq ($(wildcard $(PYTHON)),)
  PYTHON := python
  PYTEST := python -m pytest
  RUFF   := python -m ruff
endif

.PHONY: serve test lint fmt install help

## Start the FastAPI dev server on port 9000
serve:
	$(PYTHON) -m uvicorn app.server:app --host 127.0.0.1 --port 9000 --reload

## Run the full pytest test suite
test:
	$(PYTEST) tests/ -v

## Run a specific test file or pattern (usage: make test-one T=tests/test_regions.py)
test-one:
	$(PYTEST) $(T) -v

## Lint all Python files with ruff
lint:
	$(RUFF) check app/ session/ tests/

## Auto-fix ruff lint issues
fmt:
	$(RUFF) check --fix app/ session/ tests/
	$(RUFF) format app/ session/ tests/

## Install Python dependencies
install:
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt

help:
	@grep -E '^##' Makefile | sed 's/## //'
