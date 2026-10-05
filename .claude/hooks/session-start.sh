#!/bin/bash
# SessionStart hook for Claude Code cloud sessions: the Linux twin of
# scripts/setup-venv.ps1. Checks out ../numpy2stl (the mesh library; pytest.ini
# also collects its tests), builds ~/.venvs/map2stl with requirements.txt +
# requirements-dev.txt and editable installs of both repos, and installs the
# npm packages and the GL libraries pymeshlab needs. Idempotent; the container
# is cached after it finishes.
# Not installed: torch / transformers (skyline ML, `-m ml` tests).
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
NUMPY2STL="$(dirname "$ROOT")/numpy2stl"
VENV="$HOME/.venvs/map2stl"

# pymeshlab's filter plugins (mesh decimation) dlopen libOpenGL; without it the
# decimation filters are silently missing and the numpy2stl decimate tests fail.
if ! ldconfig -p | grep -q libOpenGL.so.0 && command -v apt-get >/dev/null; then
  apt-get install -y -q libopengl0 libgl1 libglu1-mesa >/dev/null \
    || { apt-get update -q >/dev/null && apt-get install -y -q libopengl0 libgl1 libglu1-mesa >/dev/null; }
fi

if [ ! -d "$NUMPY2STL/.git" ]; then
  GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 \
    https://github.com/EdgarCardenasDeLaHoz/numpy2stl "$NUMPY2STL"
else
  git -C "$NUMPY2STL" pull --ff-only --quiet || echo "numpy2stl: pull skipped"
fi

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet \
  -r "$ROOT/requirements.txt" -r "$ROOT/requirements-dev.txt"
"$VENV/bin/python" -m pip install --quiet --no-deps -e "$NUMPY2STL" -e "$ROOT"

(cd "$ROOT" && npm install --no-audit --no-fund --loglevel=error)

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export VIRTUAL_ENV=\"$VENV\"" >> "$CLAUDE_ENV_FILE"
  echo "export PATH=\"$VENV/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi
