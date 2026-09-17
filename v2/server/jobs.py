"""Background export jobs.

Three differences from v1's task registry, each of them a thing that went wrong there:

Cancel actually cancels. v1 exposed no cancel route at all; the best the browser could do
was stop polling, while the worker thread ran the render to completion and wrote a file
nobody would ever ask for. Here a cancel sets a flag the pipeline checks between stages,
and the thread unwinds.

Progress is pushed, not polled. v1's client polled every 250 ms for the whole duration of a
render — for a large export that is thousands of requests to learn a number that changed
maybe eight times. Subscribers here wait on a condition variable and are woken when
something actually happens.

Failures carry their cause. A job that fails records the exception text and keeps it until
the job is swept, so the UI can show what went wrong instead of a bare "export failed".
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from .config import JOB_TTL_SECONDS

logger = logging.getLogger(__name__)

JobState = str  # "running" | "done" | "failed" | "cancelled"


@dataclass
class Job:
    job_id: str
    kind: str
    state: JobState = "running"
    percent: int = 0
    message: str = "Starting"
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    file_path: str | None = None
    file_name: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def terminal(self) -> bool:
        return self.state != "running"

    def snapshot(self) -> dict[str, Any]:
        return {
            "jobId": self.job_id,
            "kind": self.kind,
            "state": self.state,
            "percent": self.percent,
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "elapsedSeconds": round((self.finished_at or time.time()) - self.created_at, 2),
        }


class JobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._jobs: dict[str, Job] = {}

    def start(self, kind: str, work: Callable[[Job], None]) -> Job:
        """Run *work* on a daemon thread. It receives the Job and reports through it."""
        job = Job(job_id=uuid.uuid4().hex[:12], kind=kind)
        with self._lock:
            self._sweep_locked()
            self._jobs[job.job_id] = job

        def runner() -> None:
            from .pipeline import Cancelled

            try:
                work(job)
                if job.state == "running":
                    self._finish(job, "done", 100, "Complete")
            except Cancelled:
                self._finish(job, "cancelled", job.percent, "Cancelled")
            except Exception as exc:  # noqa: BLE001 - the message is the product here
                logger.exception("Job %s failed", job.job_id)
                self._finish(job, "failed", job.percent, "Failed", error=str(exc))

        threading.Thread(target=runner, name=f"v2-{kind}-{job.job_id}", daemon=True).start()
        return job

    def update(self, job: Job, percent: int, message: str) -> None:
        with self._changed:
            if job.terminal:
                return
            job.percent = max(0, min(100, int(percent)))
            job.message = message
            self._changed.notify_all()

    def _finish(
        self,
        job: Job,
        state: JobState,
        percent: int,
        message: str,
        error: str | None = None,
    ) -> None:
        with self._changed:
            job.state = state
            job.percent = percent
            job.message = message
            job.error = error
            job.finished_at = time.time()
            self._changed.notify_all()
        if state != "done":
            self._discard_file(job)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Ask a running job to stop. Returns False if it was already finished."""
        job = self.get(job_id)
        if job is None or job.terminal:
            return False
        job._cancel.set()
        # Report the request straight away. The worker still has to reach its next check,
        # but the user asked half a second ago and should not watch a stale progress bar.
        self.update(job, job.percent, "Cancelling")
        return True

    def is_cancelled(self, job: Job) -> bool:
        return job._cancel.is_set()

    def watch(self, job_id: str, timeout: float = 300.0) -> Iterator[dict[str, Any]]:
        """Yield a snapshot on every change until the job ends or *timeout* elapses.

        The first yield is immediate, so a subscriber that arrives after the job finished
        still learns the outcome rather than blocking until the timeout.
        """
        deadline = time.time() + timeout
        last: tuple | None = None
        while True:
            with self._changed:
                job = self._jobs.get(job_id)
                if job is None:
                    yield {"jobId": job_id, "state": "unknown", "error": "no such job"}
                    return
                key = (job.state, job.percent, job.message)
                if key != last:
                    last = key
                    snapshot = job.snapshot()
                else:
                    snapshot = None
                if snapshot is None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return
                    self._changed.wait(min(remaining, 15.0))
                    continue
                terminal = job.terminal
            yield snapshot
            if terminal:
                return

    def take_file(self, job_id: str) -> tuple[str, str] | None:
        """Return (path, download_name) for a completed job, or None."""
        job = self.get(job_id)
        if job is None or job.state != "done" or not job.file_path:
            return None
        if not os.path.exists(job.file_path):
            return None
        return job.file_path, job.file_name or os.path.basename(job.file_path)

    def _discard_file(self, job: Job) -> None:
        if job.file_path and os.path.exists(job.file_path):
            try:
                os.unlink(job.file_path)
            except OSError as exc:  # pragma: no cover
                logger.warning("Could not remove %s: %s", job.file_path, exc)
        job.file_path = None

    def _sweep_locked(self) -> None:
        cutoff = time.time() - JOB_TTL_SECONDS
        for job_id in [k for k, j in self._jobs.items() if (j.finished_at or j.created_at) < cutoff]:
            self._discard_file(self._jobs[job_id])
            del self._jobs[job_id]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "count": len(self._jobs),
                "running": sum(1 for j in self._jobs.values() if not j.terminal),
            }


jobs = JobRegistry()
