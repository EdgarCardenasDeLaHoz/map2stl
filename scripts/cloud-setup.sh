#!/usr/bin/env bash
# Set up map2stl for tests and refactors in a Linux cloud session (no GPU).
#
#   bash scripts/cloud-setup.sh            # venv at ~/.venvs/map2stl, then a quick import check
#   ~/.venvs/map2stl/bin/python -m pytest -n auto -q
#
# Local Windows PCs use scripts/setup-venv.ps1 instead (CUDA torch, nbstripout, hooks).
# What this does:
#   1. clones the sibling numpy2stl repo next to this one if it is missing (pytest.ini
#      collects ../numpy2stl/tests, and map2stl imports numpy2stl);
#   2. creates the venv outside the repo (~/.venvs/map2stl; override with VENV=...);
#   3. installs the pinned requirements, the dev/test tools, the CPU torch wheel (optional:
#      skipped with a warning when download.pytorch.org is unreachable), and both repos as
#      editable packages; the libOpenGL that pymeshlab needs (apt, as root); npm packages.
# Tests that need what a fresh clone lacks (the gitignored cache/, API keys, a GPU, the
# network) skip themselves: see the requires_* markers in pytest.ini and tests/conftest.py.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARENT="$(dirname "$ROOT")"
NUMPY2STL="$PARENT/numpy2stl"
VENV="${VENV:-$HOME/.venvs/map2stl}"
PY="${PYTHON:-python3}"

if [ ! -d "$NUMPY2STL/.git" ] && [ ! -f "$NUMPY2STL/.git" ]; then
  echo "Cloning numpy2stl next to map2stl ($NUMPY2STL)"
  git clone --depth 1 https://github.com/EdgarCardenasDeLaHoz/numpy2stl.git "$NUMPY2STL"
fi

# pymeshlab's filter plugins dlopen libOpenGL; without it the decimation filters are
# silently missing and the numpy2stl decimation tests fail (seen on a fresh Linux image).
if ! ldconfig -p 2>/dev/null | grep -q libOpenGL.so.0 && command -v apt-get >/dev/null \
    && [ "$(id -u)" = 0 ]; then
  apt-get install -y -q libopengl0 libgl1 libglu1-mesa >/dev/null \
    || { apt-get update -q >/dev/null && apt-get install -y -q libopengl0 libgl1 libglu1-mesa >/dev/null; } \
    || echo "WARNING: could not install libOpenGL; numpy2stl decimation tests will fail"
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating venv $VENV"
  "$PY" -m venv "$VENV"
fi
VPY="$VENV/bin/python"
"$VPY" -m pip install --upgrade pip wheel

# CPU torch first, so nothing below pulls the large CUDA wheel from PyPI. Optional: only
# the opt-in -m ml tests and the skyline models need it, and some cloud network policies
# block download.pytorch.org (proxy 403), which used to abort the whole setup here.
"$VPY" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu \
  || echo "WARNING: CPU torch not installed (download.pytorch.org unreachable); -m ml tests and skyline models unavailable"
"$VPY" -m pip install -r "$ROOT/requirements.txt" -r "$ROOT/requirements-dev.txt"
"$VPY" -m pip install pytest-xdist pytest-testmon
"$VPY" -m pip install --no-deps -e "$NUMPY2STL" -e "$ROOT"

# Bytecode out of the repo, as on the local PCs.
SITE="$("$VPY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
echo "import sys; sys.pycache_prefix = '$HOME/.cache/pycache'" > "$SITE/zz_pycache_prefix.pth"

# JS tests (vitest), eslint and the Vite build.
if command -v npm >/dev/null; then
  (cd "$ROOT" && npm install --no-audit --no-fund --loglevel=error)
fi

"$VPY" -c "import app.server.server, numpy2stl, geo2stl, city2stl; print('imports ok')"
echo "Ready: $VPY -m pytest -n auto -q"
