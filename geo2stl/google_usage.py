"""Google paid-request counter (F-UNIFY Phase 4).

Counts, per calendar month, the requests that Google bills against a free monthly cap, so the
app can say "Street View this month 1,840 / 10,000" before it spends any of it.

* ``streetview_image`` — Street View Static *image* requests (cap 10,000 a month). Metadata
  requests are free and unlimited, so they are not counted.
* ``tiles_root`` — Map Tiles API 3D Tiles ``root.json`` requests (cap 1,000 a month). Each one
  opens a session; the tiles under it are not counted.

A count is recorded where the request is **sent** (``city2stl.skyline.streetview_io._streetview_get``,
``city2stl.height.providers.google_3d``, the photo-label proxy, ``tools/eval/pano_manifest``), so a
cache hit never counts and a retry does (Google may bill it). The file is ``output/usage/
google_usage.json`` (git-ignored; ``MAP2STL_GOOGLE_USAGE_FILE`` overrides it), written under
``geo2stl.cache.file_lock`` because the app, the skyline CLI and a benchmark may run at once.

``cache_only()`` is the server's switch for a run that must not spend anything: while it is
active :func:`paid_allowed` is False, and the send points skip the request and call
:func:`note_blocked` instead. It is per job: a ``ContextVar``, so a cache-only run does not block
a paid job running at the same time (and the reverse). Worker threads do not inherit a
context: submit their work through :func:`bind` (``pool.map(bind(fn), ...)``).
"""

from __future__ import annotations

import contextvars
import functools
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from geo2stl.cache import atomic_write_json, file_lock

logger = logging.getLogger(__name__)

#: Free monthly caps (requests) per kind.
CAPS = {"streetview_image": 10_000, "tiles_root": 1_000}
LABELS = {"streetview_image": "Street View images", "tiles_root": "3D Tiles root requests"}

_ROOT = Path(__file__).resolve().parents[1]
_thread_lock = threading.Lock()
_state_lock = threading.Lock()
#: The active cache-only scope of this job: a dict that collects the blocked counts, or None.
_scope: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar(
    "google_usage_scope", default=None)
_blocked: dict[str, int] = {}


def usage_path() -> Path:
    env = os.environ.get("MAP2STL_GOOGLE_USAGE_FILE")
    return Path(env) if env else _ROOT / "output" / "usage" / "google_usage.json"


def current_month() -> str:
    """``YYYY-MM`` in local time (Google's quota resets on the calendar month)."""
    return time.strftime("%Y-%m")


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record(kind: str, n: int = 1, *, month: str | None = None) -> int:
    """Add ``n`` paid requests of ``kind`` to this month's count and return the new count.

    Never raises: a counter that cannot be written must not stop a run (it logs once per call).
    """
    if kind not in CAPS:
        raise ValueError(f"unknown Google usage kind {kind!r}")
    month = month or current_month()
    path = usage_path()
    try:
        with _thread_lock, file_lock(path, timeout_s=10):
            data = _read(path)
            months = data.setdefault("months", {})
            row = months.setdefault(month, {})
            row[kind] = int(row.get(kind, 0)) + int(n)
            atomic_write_json(path, data, indent=1, sort_keys=True)
            return row[kind]
    except Exception as exc:  # noqa: BLE001 - counting must never break a fetch
        logger.warning("Google usage counter not written: %s", exc)
        return 0


def month_counts(month: str | None = None) -> dict[str, dict]:
    """``{kind: {"count", "cap"}}`` for ``month`` (default this month); every kind is present."""
    month = month or current_month()
    row = (_read(usage_path()).get("months") or {}).get(month) or {}
    return {k: {"count": int(row.get(k, 0)), "cap": cap} for k, cap in CAPS.items()}


# ----------------------------------------------------------------------- cache-only switch


@contextmanager
def cache_only():
    """While active in this job (thread/context), :func:`paid_allowed` is False. Yields a dict
    that collects what the send points skipped (``{kind: count}``). Nested uses share the
    outer scope."""
    scope = _scope.get()
    token = None
    if scope is None:
        scope = {}
        token = _scope.set(scope)
    try:
        yield scope
    finally:
        if token is not None:
            _scope.reset(token)


def paid_allowed(kind: str | None = None) -> bool:  # noqa: ARG001 - kind reserved
    """False inside :func:`cache_only` of the calling job: the caller must skip the request."""
    return _scope.get() is None


def bind(fn):
    """``fn`` that runs with the caller's cache-only scope, for pool worker threads (a thread
    does not inherit contextvars). Call it in the submitting thread: ``pool.map(bind(fn), xs)``."""
    scope = _scope.get()

    @functools.wraps(fn)
    def run(*args, **kwargs):
        token = _scope.set(scope)
        try:
            return fn(*args, **kwargs)
        finally:
            _scope.reset(token)

    return run


def note_blocked(kind: str, n: int = 1) -> None:
    """A send point skipped a paid request because of :func:`cache_only`."""
    with _state_lock:
        scope = _scope.get()
        if scope is not None:
            scope[kind] = scope.get(kind, 0) + n
        _blocked[kind] = _blocked.get(kind, 0) + n


def blocked_counts(reset: bool = False) -> dict[str, int]:
    with _state_lock:
        out = dict(_blocked)
        if reset:
            _blocked.clear()
    return out
