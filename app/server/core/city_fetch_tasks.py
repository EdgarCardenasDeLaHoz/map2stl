"""
core/city_fetch_tasks.py — OSM city-layer fetches as cancellable background jobs.

``POST /api/cities`` blocks for the whole Overpass round trip (tens of seconds to
minutes for a 9-layer city), with no way to see which layer is slow, which mirror
is in use, or to stop it. This registry runs the same ``core/city_data.get_city_layers``
in a thread and records per-layer state from its progress hooks, so the client can
poll ``GET /api/cities/status/{id}`` and cancel with ``POST /api/cities/cancel/{id}``.

Runs on the shared job runner (``core/jobs.py``: identical running requests join,
finished tasks swept after 300 s); a city fetch ends in a JSON payload and adds
per-layer state.

Layer states: pending -> fetching -> done | failed, or cached (served from disk).
Cancellation stops layers that have not started; Overpass queries already in
flight run to completion in the worker and their result is discarded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.server.core.jobs import Job, JobRegistry
from city2stl.fetch import FetchCancelled

logger = logging.getLogger(__name__)


@dataclass
class CityFetchTask(Job):
    """State of one background city fetch (statuses: running | done | error | cancelled)."""
    layers: list[str] = field(default_factory=list)
    layer_states: dict[str, str] = field(default_factory=dict)
    mirror: str | None = None

    def cancel(self) -> None:
        if self.running:
            for name, state in self.layer_states.items():
                if state in ("pending", "fetching"):
                    self.layer_states[name] = "cancelled"
        super().cancel()

    def snapshot(self) -> dict:
        """JSON-safe status for ``GET /api/cities/status/{id}``."""
        return {
            "task_id": self.task_id,
            "status": self.status,
            "layers": [{"name": name, "state": state}
                       for name, state in self.layer_states.items()],
            "mirror": self.mirror,
            "message": self.message,
            "error": self.error,
            "elapsed_s": self.elapsed_s(),
            "diagonal_km": self.params.get("diagonal_km"),
        }


_registry = JobRegistry("city-fetch", ttl=300)


def get_task(task_id: str) -> CityFetchTask | None:
    return _registry.get(task_id)


def start_city_fetch(north: float, south: float, east: float, west: float,
                     layers: list[str], simplify_tolerance: float, min_area: float,
                     diagonal_km: float | None = None, roof_shapes="auto") -> CityFetchTask:
    """Start (or join) a background fetch; returns its task.

    A request identical to one still running joins it instead of starting a
    second Overpass fetch for the same data (the client asks from several places).
    """
    # Late import: tests patch app.server.core.city_data.fetch_osm_data.
    from app.server.core import city_data

    params = {"north": north, "south": south, "east": east, "west": west,
              "layers": list(layers), "simplify_tolerance": simplify_tolerance,
              "min_area": min_area, "diagonal_km": diagonal_km,
              "roof_shapes": city_data.roof_shapes_mode(roof_shapes)}

    def run(task: CityFetchTask) -> None:
        def progress(layer: str, state: str) -> None:
            task.layer_states[layer] = state
            busy = [n for n, s in task.layer_states.items() if s == "fetching"]
            task.message = f"Fetching {', '.join(busy)}…" if busy else task.message

        def roof_progress(done: int, total: int) -> None:
            task.message = f"Classifying roofs {done:,} of {total:,}…"

        def on_mirror(endpoint: str) -> None:
            task.mirror = endpoint

        try:
            result = city_data.get_city_layers(
                north, south, east, west, layers, simplify_tolerance, min_area,
                progress=progress, on_mirror=on_mirror,
                should_cancel=task.cancel_event.is_set, roof_shapes=roof_shapes,
                roof_progress=roof_progress)
        except FetchCancelled:
            task.finish("cancelled", "Cancelled")
            return
        except Exception as exc:
            logger.warning("City fetch task %s failed: %s", task.task_id, exc)
            for name, state in task.layer_states.items():
                if state in ("pending", "fetching"):
                    task.layer_states[name] = "failed"
            task.error = str(exc)
            task.finish("error", f"OSM fetch failed: {exc}")
            return
        if task.cancel_event.is_set():
            task.finish("cancelled", "Cancelled")
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
        task.finish("done", "Complete")

    def make(task_id: str) -> CityFetchTask:
        return CityFetchTask(task_id=task_id, params=params, layers=list(layers),
                             layer_states={name: "pending" for name in layers})

    return _registry.start(make, run, join_identical=params)


def cancel_task(task_id: str) -> CityFetchTask | None:
    """Ask a running fetch to stop; returns the task (None if unknown)."""
    return _registry.cancel(task_id)
