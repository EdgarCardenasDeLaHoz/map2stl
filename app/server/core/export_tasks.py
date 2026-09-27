"""
core/export_tasks.py — Async export task tracking and lifecycle.

Manages the state of long-running export jobs (STL/OBJ/3MF generation).
Tracks progress, stores results in temp files, handles cleanup.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field

from starlette.background import BackgroundTask

logger = logging.getLogger(__name__)


@dataclass
class ExportTask:
    """Tracks the state of an async export job."""
    task_id: str
    status: str = "running"          # running | complete | error
    progress: int = 0                # 0-100
    message: str = "Starting..."
    result_path: str | None = None
    filename: str | None = None
    media_type: str = "application/octet-stream"
    headers: dict[str, str] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    finished: float | None = None

    def update(self, progress: int, message: str) -> None:
        self.progress = progress
        self.message = message

    def complete(self, result_path: str, filename: str, headers: dict = None) -> None:
        self.status = "complete"
        self.finished = time.time()
        self.progress = 100
        self.message = "Complete"
        self.result_path = result_path
        self.filename = filename
        if headers:
            self.headers = headers

    def fail(self, message: str) -> None:
        self.status = "error"
        self.finished = time.time()
        self.message = message


_export_tasks: dict[str, ExportTask] = {}
_export_tasks_lock = threading.Lock()
_TASK_TTL = 300  # seconds before stale tasks are cleaned up


def _cleanup_stale_tasks() -> None:
    """Remove tasks that finished more than _TASK_TTL seconds ago.

    Running tasks are never swept: a city model can take several minutes, and
    sweeping by creation time deleted it before the client could download it.
    """
    cutoff = time.time() - _TASK_TTL
    with _export_tasks_lock:
        stale = [tid for tid, t in _export_tasks.items()
                 if t.finished is not None and t.finished < cutoff]
    for tid in stale:
        with _export_tasks_lock:
            task = _export_tasks.pop(tid, None)
        if task and task.result_path and os.path.exists(task.result_path):
            try:
                os.unlink(task.result_path)
            except OSError:
                pass


def get_task_status(task_id: str) -> dict | None:
    """Return progress info for a task, or None if not found."""
    with _export_tasks_lock:
        task = _export_tasks.get(task_id)
    if task is None:
        return None
    return {
        "task_id": task.task_id,
        "status": task.status,
        "progress": task.progress,
        "message": task.message,
    }


def get_task_file(task_id: str):
    """Return a FileResponse for a completed task, or None."""
    from fastapi.responses import FileResponse
    with _export_tasks_lock:
        task = _export_tasks.get(task_id)
    if not task or task.status != "complete" or not task.result_path:
        return None

    def _cleanup():
        try:
            os.unlink(task.result_path)
        except OSError:
            pass
        with _export_tasks_lock:
            _export_tasks.pop(task_id, None)

    return FileResponse(
        task.result_path,
        filename=task.filename,
        media_type=task.media_type,
        background=BackgroundTask(_cleanup),
        headers=task.headers,
    )


def start_export_task(data: dict, fmt: str) -> str:
    """Start an export in a background thread. Returns task_id."""
    # Lazy imports to avoid circular dependency with export.py
    from app.server.core.export import _run_export_pipeline, generate_puzzle

    _cleanup_stale_tasks()

    task_id = uuid.uuid4().hex[:12]
    task = ExportTask(task_id=task_id)
    with _export_tasks_lock:
        _export_tasks[task_id] = task

    def _run():
        try:
            if fmt == "puzzle":
                generate_puzzle(data, task)
            elif fmt == "city":
                from app.server.core.city_model_task import run_city_model
                run_city_model(data, task)
            else:
                _run_export_pipeline(data, fmt, task)
        except Exception as exc:
            logger.exception("Export task %s failed", task_id)
            task.fail(str(exc))

    thread = threading.Thread(target=_run, daemon=True, name=f"export-{task_id}")
    thread.start()
    return task_id
