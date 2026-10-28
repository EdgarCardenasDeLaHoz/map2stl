"""
core/jobs.py — the one background-job runner (F-UNIFY Phase 1).

Every long server job (exports, city fetches, plate registrations; later skyline
runs and benchmarks) is a ``Job`` in a ``JobRegistry``:

* a job runs in a daemon thread named ``<kind>-<job_id>``;
* the registry is a dict + lock; jobs that finished more than ``ttl`` seconds ago are
  swept on the next start (running jobs are never swept: a city model can take minutes);
* ``join_identical`` lets a request identical to a running one join it instead of
  starting the same work twice (the client asks from several places);
* cancel sets ``cancel_event`` and marks the job cancelled at once; a worker that
  cannot stop mid-step finishes and its result is discarded (``Job.finish`` ignores it).

Each kind keeps its own status words and snapshot (the client already reads them:
exports end ``complete``, fetches and plates ``done``), so this module only owns the
lifecycle. ``GET /api/jobs/{id}`` (``routers/jobs.py``) finds a job of any kind.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

RUNNING = "running"
CANCELLED = "cancelled"


@dataclass
class Job:
    """Lifecycle state shared by every kind of job."""
    task_id: str
    params: dict = field(default_factory=dict)
    status: str = RUNNING
    progress: int = 0                # 0-100
    message: str = "Starting…"
    error: str | None = None
    result: dict | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    updated: float = field(default_factory=time.time)   # last progress/message change
    cancel_event: threading.Event = field(default_factory=threading.Event,
                                          repr=False, compare=False)
    # The worker; its liveness is the heartbeat (one step can run minutes without
    # a progress change, so an unchanged status is not a stall while it lives).
    thread: threading.Thread | None = field(default=None, repr=False, compare=False)

    @property
    def running(self) -> bool:
        return self.status == RUNNING

    def alive(self) -> bool:
        """True while the job is running and its worker thread is alive."""
        return self.running and (self.thread is None or self.thread.is_alive())

    def update(self, progress: int, message: str) -> None:
        if progress != self.progress or message != self.message:
            self.updated = time.time()
        self.progress = progress
        self.message = message

    def finish(self, status: str, message: str | None = None) -> None:
        """End the job, unless a cancel already ended it (then only stamp the time)."""
        if self.status == CANCELLED:
            self.finished = self.finished or time.time()
            return
        self.status = status
        if message is not None:
            self.message = message
        self.finished = time.time()

    def cancel(self) -> None:
        if self.running:
            self.cancel_event.set()
            self.status, self.message = CANCELLED, "Cancelled"
            self.finished = time.time()

    def elapsed_s(self) -> float:
        return round((self.finished or time.time()) - self.created, 1)

    def snapshot(self) -> dict:
        """JSON-safe status common to every kind; kinds extend it."""
        return {"task_id": self.task_id, "status": self.status,
                "progress": self.progress, "message": self.message,
                "error": self.error, "alive": self.alive(),
                "elapsed_s": self.elapsed_s(),
                "idle_s": round(time.time() - self.updated, 1)}


def _params_key(params: dict) -> tuple:
    return tuple(sorted((k, tuple(v) if isinstance(v, list) else v)
                        for k, v in params.items()))


_registries: list[JobRegistry] = []


class JobRegistry:
    """The jobs of one kind: start, get, cancel, TTL sweep."""

    def __init__(self, kind: str, ttl: float = 300,
                 on_sweep: Callable[[Job], None] | None = None):
        self.kind = kind
        self.ttl = ttl
        self.on_sweep = on_sweep          # e.g. delete the job's temp files
        self.tasks: dict[str, Job] = {}
        self.lock = threading.Lock()
        _registries.append(self)

    def sweep(self) -> None:
        cutoff = time.time() - self.ttl
        with self.lock:
            stale = [self.tasks.pop(tid) for tid, t in list(self.tasks.items())
                     if t.finished is not None and t.finished < cutoff]
        if self.on_sweep:
            for job in stale:
                self.on_sweep(job)

    def get(self, task_id: str) -> Job | None:
        with self.lock:
            return self.tasks.get(task_id)

    def pop(self, task_id: str) -> Job | None:
        with self.lock:
            return self.tasks.pop(task_id, None)

    def add(self, job: Job) -> Job:
        with self.lock:
            self.tasks[job.task_id] = job
        return job

    def start(self, make: Callable[[str], Job], work: Callable[[Job], None], *,
              join_identical: dict | None = None) -> Job:
        """Create a job with ``make(job_id)`` and run ``work(job)`` in a thread.

        With ``join_identical`` (the job's params), a running job of this kind with
        the same params is returned instead. ``work`` reports through the job; an
        exception it raises fails the job with its message.
        """
        self.sweep()
        with self.lock:
            if join_identical is not None:
                key = _params_key(join_identical)
                for job in self.tasks.values():
                    if job.running and _params_key(job.params) == key:
                        return job
            job = make(uuid.uuid4().hex[:12])
            self.tasks[job.task_id] = job

        def run() -> None:
            try:
                work(job)
            except Exception as exc:
                logger.exception("%s job %s failed", self.kind, job.task_id)
                job.error = str(exc)
                job.finish("error", str(exc))

        job.thread = threading.Thread(target=run, daemon=True,
                                      name=f"{self.kind}-{job.task_id}")
        job.thread.start()
        return job

    def cancel(self, task_id: str) -> Job | None:
        job = self.get(task_id)
        if job is not None:
            job.cancel()
        return job


def find(task_id: str) -> tuple[str, Job] | None:
    """The job with this id in any registry, as ``(kind, job)``."""
    for reg in _registries:
        job = reg.get(task_id)
        if job is not None:
            return reg.kind, job
    return None
