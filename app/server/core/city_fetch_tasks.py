"""
core/city_fetch_tasks.py — OSM city-layer fetches as cancellable background jobs.

``POST /api/cities`` blocks for the whole Overpass round trip (tens of seconds to
minutes for a 9-layer city), with no way to see which layer is slow, which mirror
is in use, or to stop it. This registry runs the same ``core/city_data.get_city_layers``
in a thread and records per-layer state from its progress hooks, so the client can
poll ``GET /api/cities/status/{id}`` and cancel with ``POST /api/cities/cancel/{id}``.

Same lifecycle pattern as ``core/export_tasks.py`` (dict + lock, finished tasks
swept after ``_TASK_TTL``), kept separate because a city fetch ends in a JSON
payload rather than a file, and has per-layer state and cancellation.

Layer states: pending -> fetching -> done | failed, or cached (served from disk).
Cancellation stops layers that have not started; Overpass queries already in
flight run to completion in the worker and their result is discarded.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

from city2stl.fetch import FetchCancelled

logger = logging.getLogger(__name__)

_TASK_TTL = 300  # seconds a finished task (and its result) is kept


@dataclass
class CityFetchTask:
    """State of one background city fetch."""
    task_id: str
    params: dict
    layers: list[str]
    status: str = "running"          # running | done | error | cancelled
    layer_states: dict[str, str] = field(default_factory=dict)
    mirror: str | None = None
    message: str = "Starting…"
    error: str | None = None
    result: dict | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict:
        """JSON-safe status for ``GET /api/cities/status/{id}``."""
        end = self.finished or time.time()
        return {
            "task_id": self.task_id,
            "status": self.status,
            "layers": [{"name": name, "state": state}
                       for name, state in self.layer_states.items()],
            "mirror": self.mirror,
            "message": self.message,
            "error": self.error,
            "elapsed_s": round(end - self.created, 1),
            "diagonal_km": self.params.get("diagonal_km"),
        }


_tasks: dict[str, CityFetchTask] = {}
_lock = threading.Lock()


def _params_key(params: dict) -> tuple:
    return tuple(sorted((k, tuple(v) if isinstance(v, list) else v)
                        for k, v in params.items()))


def _cleanup() -> None:
    cutoff = time.time() - _TASK_TTL
    with _lock:
        for tid in [tid for tid, t in _tasks.items()
                    if t.finished is not None and t.finished < cutoff]:
            _tasks.pop(tid, None)


def get_task(task_id: str) -> CityFetchTask | None:
    with _lock:
        return _tasks.get(task_id)


def start_city_fetch(north: float, south: float, east: float, west: float,
                     layers: list[str], simplify_tolerance: float, min_area: float,
                     diagonal_km: float | None = None) -> CityFetchTask:
    """Start (or join) a background fetch; returns its task.

    A request identical to one still running joins it instead of starting a
    second Overpass fetch for the same data (the client asks from several places).
    """
    # Late import: tests patch app.server.core.city_data.fetch_osm_data.
    from app.server.core import city_data

    _cleanup()
    params = {"north": north, "south": south, "east": east, "west": west,
              "layers": list(layers), "simplify_tolerance": simplify_tolerance,
              "min_area": min_area, "diagonal_km": diagonal_km}
    key = _params_key(params)
    with _lock:
        for task in _tasks.values():
            if task.status == "running" and _params_key(task.params) == key:
                return task
        task = CityFetchTask(task_id=uuid.uuid4().hex[:12], params=params,
                             layers=list(layers),
                             layer_states={name: "pending" for name in layers})
        _tasks[task.task_id] = task

    def progress(layer: str, state: str) -> None:
        task.layer_states[layer] = state
        busy = [n for n, s in task.layer_states.items() if s == "fetching"]
        task.message = f"Fetching {', '.join(busy)}…" if busy else task.message

    def on_mirror(endpoint: str) -> None:
        task.mirror = endpoint

    def run() -> None:
        try:
            result = city_data.get_city_layers(
                north, south, east, west, layers, simplify_tolerance, min_area,
                progress=progress, on_mirror=on_mirror,
                should_cancel=task.cancel_event.is_set)
        except FetchCancelled:
            _finish(task, "cancelled", "Cancelled")
            return
        except Exception as exc:
            logger.warning("City fetch task %s failed: %s", task.task_id, exc)
            for name, state in task.layer_states.items():
                if state in ("pending", "fetching"):
                    task.layer_states[name] = "failed"
            task.error = str(exc)
            _finish(task, "error", f"OSM fetch failed: {exc}")
            return
        if task.cancel_event.is_set():
            _finish(task, "cancelled", "Cancelled")
            return
        result = dict(result)
        if diagonal_km is not None:
            result["diagonal_km"] = round(diagonal_km, 2)
        for name in layers:
            fc = result.get(name)
            if task.layer_states.get(name) in ("pending", "fetching"):
                task.layer_states[name] = "failed" if (
                    isinstance(fc, dict) and fc.get("error")) else "done"
        task.result = result
        _finish(task, "done", "Complete")

    threading.Thread(target=run, daemon=True, name=f"city-fetch-{task.task_id}").start()
    return task


def _finish(task: CityFetchTask, status: str, message: str) -> None:
    if task.status == "cancelled":   # cancel() already answered the client
        task.finished = task.finished or time.time()
        return
    task.status = status
    task.message = message
    task.finished = time.time()


def cancel_task(task_id: str) -> CityFetchTask | None:
    """Ask a running fetch to stop; returns the task (None if unknown)."""
    task = get_task(task_id)
    if task is None:
        return None
    if task.status == "running":
        task.cancel_event.set()
        task.status = "cancelled"
        task.message = "Cancelled"
        task.finished = time.time()
        for name, state in task.layer_states.items():
            if state in ("pending", "fetching"):
                task.layer_states[name] = "cancelled"
    return task
