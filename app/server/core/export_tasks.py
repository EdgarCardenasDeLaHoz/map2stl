"""
core/export_tasks.py — Async export task tracking and lifecycle.

Manages the state of long-running export jobs (STL/OBJ/3MF generation).
Tracks progress, stores results in temp files, handles cleanup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from starlette.background import BackgroundTask

from app.server.core.jobs import Job, JobRegistry


@dataclass
class ExportTask(Job):
    """An async export job (statuses: running | complete | error)."""
    message: str = "Starting..."
    result_path: str | None = None
    # City builds: the model's parts for the Extrude viewer (numpy2stl.io.write_parts_file).
    viewer_path: str | None = None
    filename: str | None = None
    media_type: str = "application/octet-stream"
    headers: dict[str, str] = field(default_factory=dict)

    def complete(self, result_path: str, filename: str, headers: dict = None) -> None:
        self.progress = 100
        self.result_path = result_path
        self.filename = filename
        if headers:
            self.headers = headers
        self.finish("complete", "Complete")

    def fail(self, message: str) -> None:
        self.finish("error", message)


def _unlink_files(task: ExportTask) -> None:
    for path in (task.result_path, task.viewer_path):
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass


# Finished tasks are swept after 300 s; running ones never are (a city model can
# take several minutes, and sweeping by creation time deleted it before download).
_registry = JobRegistry("export", ttl=300, on_sweep=_unlink_files)
_export_tasks = _registry.tasks
_export_tasks_lock = _registry.lock


def get_task_status(task_id: str) -> dict | None:
    """Return progress info for a task, or None if not found.

    ``alive`` is the heartbeat (the worker thread is still running), ``elapsed_s``
    the time since the task started and ``idle_s`` the time since progress or
    message last changed. export-handlers.js keeps polling while ``alive`` or
    the status changes, instead of giving up after a fixed wall-clock time.
    """
    task = _registry.get(task_id)
    if task is None:
        return None
    snap = task.snapshot()
    snap.pop("error")
    return snap


def get_task_file(task_id: str):
    """Return a FileResponse for a completed task, or None."""
    from fastapi.responses import FileResponse
    task = _registry.get(task_id)
    if not task or task.status != "complete" or not task.result_path:
        return None

    def _cleanup():
        _unlink_files(task)
        _registry.pop(task_id)

    return FileResponse(
        task.result_path,
        filename=task.filename,
        media_type=task.media_type,
        background=BackgroundTask(_cleanup),
        headers=task.headers,
    )


def get_task_viewer_file(task_id: str):
    """The finished city model's parts for the viewer (``numpy2stl.io.write_parts_file``), or None.

    Unlike the download, reading it keeps the task: the viewer reads it, the user may
    still download, and the TTL sweep removes both files.
    """
    from fastapi.responses import FileResponse
    task = _registry.get(task_id)
    if not task or task.status != "complete" or not task.viewer_path             or not os.path.exists(task.viewer_path):
        return None
    return FileResponse(task.viewer_path, media_type="application/octet-stream")


def start_export_task(data: dict, fmt: str) -> str:
    """Start an export in a background thread. Returns task_id."""
    # Lazy imports to avoid circular dependency with export.py
    from app.server.core.export import _run_export_pipeline, generate_puzzle

    def work(task: ExportTask) -> None:
        if fmt == "puzzle":
            generate_puzzle(data, task)
        elif fmt == "city":
            from app.server.core.city_model_task import run_city_model
            run_city_model(data, task)
        else:
            _run_export_pipeline(data, fmt, task)

    return _registry.start(lambda tid: ExportTask(task_id=tid), work).task_id
