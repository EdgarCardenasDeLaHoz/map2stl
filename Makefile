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

.PHONY: serve test lint fmt install clean-runs help

## Start the FastAPI dev server on port 9000
serve:
	$(PYTHON) -m uvicorn app.server.server:app --host 127.0.0.1 --port 9000 --reload

## Run the full pytest test suite
test:
	$(PYTEST) tests/ -v

## Run a specific test file or pattern (usage: make test-one T=tests/test_regions.py)
test-one:
	$(PYTEST) $(T) -v

## Lint all Python files with ruff
lint:
	$(RUFF) check app/ tests/

## Auto-fix ruff lint issues
fmt:
	$(RUFF) check --fix app/ tests/
	$(RUFF) format app/ tests/

## Install Python dependencies
install:
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt

## Delete regenerable skyline caches under city2stl/skyline/runs (frees GBs; keeps region_reports/ + height_traces/)
clean-runs:
	$(PYTHON) -c "import shutil, pathlib; b = pathlib.Path('city2stl/skyline/runs'); dirs = ['image_cache', 'satellite_image_cache', 'satellite_footprints_cache', 'screen_cache']; [(print('rm', b / d), shutil.rmtree(b / d, ignore_errors=True)) for d in dirs]; [(print('rm', f), f.unlink()) for f in b.glob('*.log')]; print('Done. Kept region_reports/ and height_traces/.')"

help:
	@grep -E '^##' Makefile | sed 's/## //'
