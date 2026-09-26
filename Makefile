# strm2stl — common dev commands
# Run from strm2stl/ (the directory containing this Makefile)

# The venv lives outside OneDrive; scripts/setup-venv.ps1 creates it.
VENV := $(USERPROFILE)/.venvs/strm2stl
PYTHON := $(VENV)/Scripts/python
PYTEST := $(PYTHON) -m pytest
RUFF := $(VENV)/Scripts/ruff

.PHONY: serve test lint fmt install clean-runs help

## Start the FastAPI dev server on port 9000
serve:
	$(PYTHON) -m uvicorn app.server.server:app --host 127.0.0.1 --port 9000 --reload

## Run the test suite (this repo + ../numpy2stl, per pytest.ini)
test:
	$(PYTEST)

## Run a specific test file or pattern (usage: make test-one T=tests/test_regions.py)
test-one:
	$(PYTEST) $(T) -v

## Lint the libraries and app with ruff
lint:
	$(RUFF) check app/ geo2stl/ city2stl/ tests/ tools/

## Auto-fix ruff lint issues
fmt:
	$(RUFF) check --fix app/ geo2stl/ city2stl/ tests/ tools/

## Create / refresh the venv and install both repos editable
install:
	powershell -ExecutionPolicy Bypass -File scripts/setup-venv.ps1

## Delete regenerable skyline caches under city2stl/skyline/runs (frees GBs; keeps region_reports/ + height_traces/)
clean-runs:
	$(PYTHON) -c "import shutil, pathlib; b = pathlib.Path('city2stl/skyline/runs'); dirs = ['image_cache', 'satellite_image_cache', 'satellite_footprints_cache', 'screen_cache']; [(print('rm', b / d), shutil.rmtree(b / d, ignore_errors=True)) for d in dirs]; [(print('rm', f), f.unlink()) for f in b.glob('*.log')]; print('Done. Kept region_reports/ and height_traces/.')"

help:
	@grep -E '^##' Makefile | sed 's/## //'
