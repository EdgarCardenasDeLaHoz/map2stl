"""The shared job runner (app/server/core/jobs.py) and GET /api/jobs/{id}."""

from __future__ import annotations

import threading
import time

import pytest

from app.server.core import jobs


@pytest.fixture
def reg():
    r = jobs.JobRegistry("test-job", ttl=60)
    yield r
    jobs._registries.remove(r)


def _wait(job, timeout=5.0):
    job.thread.join(timeout)
    assert not job.thread.is_alive()


def test_start_runs_work_in_named_thread(reg):
    seen = {}

    def work(job):
        seen["name"] = threading.current_thread().name
        job.update(50, "half")
        job.result = {"ok": 1}
        job.finish("done", "Complete")

    job = reg.start(lambda tid: jobs.Job(task_id=tid), work)
    _wait(job)
    assert seen["name"] == f"test-job-{job.task_id}"
    assert (job.status, job.message, job.result) == ("done", "Complete", {"ok": 1})
    assert reg.get(job.task_id) is job and jobs.find(job.task_id) == ("test-job", job)


def test_exception_fails_the_job(reg):
    def work(job):
        raise RuntimeError("boom")

    job = reg.start(lambda tid: jobs.Job(task_id=tid), work)
    _wait(job)
    assert (job.status, job.error, job.message) == ("error", "boom", "boom")
    assert job.finished is not None


def test_identical_running_request_joins(reg):
    gate = threading.Event()
    calls = []

    def work(job):
        calls.append(job.task_id)
        gate.wait(5)
        job.finish("done")

    params = {"a": 1, "layers": ["x", "y"]}
    first = reg.start(lambda tid: jobs.Job(task_id=tid, params=params), work,
                      join_identical=params)
    again = reg.start(lambda tid: jobs.Job(task_id=tid, params=dict(params)), work,
                      join_identical=dict(params))
    other = reg.start(lambda tid: jobs.Job(task_id=tid, params={"a": 2}), work,
                      join_identical={"a": 2})
    gate.set()
    _wait(first)
    _wait(other)
    assert again is first and other is not first
    assert len(calls) == 2


def test_cancel_wins_over_a_late_finish(reg):
    gate = threading.Event()

    def work(job):
        gate.wait(5)
        job.result = {"late": True}
        job.finish("done", "Complete")

    job = reg.start(lambda tid: jobs.Job(task_id=tid), work)
    assert reg.cancel(job.task_id) is job
    assert job.cancel_event.is_set() and job.status == "cancelled"
    gate.set()
    _wait(job)
    assert (job.status, job.message) == ("cancelled", "Cancelled")
    assert reg.cancel("nope") is None


def test_sweep_drops_only_finished_jobs_past_ttl(reg):
    swept = []
    reg.on_sweep = swept.append
    old = reg.add(jobs.Job(task_id="old"))
    old.finish("done")
    old.finished -= 120
    fresh = reg.add(jobs.Job(task_id="fresh"))
    fresh.finish("done")
    running = reg.add(jobs.Job(task_id="run", created=time.time() - 999))
    reg.sweep()
    assert swept == [old]
    assert reg.get("old") is None and reg.get("fresh") is fresh and reg.get("run") is running


def test_jobs_endpoint_finds_any_kind(client, reg):
    job = reg.add(jobs.Job(task_id="ep1", message="working"))
    r = client.get("/api/jobs/ep1")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "test-job" and body["status"] == "running"
    assert body["message"] == "working"
    r = client.post("/api/jobs/ep1/cancel")
    assert r.json()["status"] == "cancelled" and job.cancel_event.is_set()
    assert client.get("/api/jobs/missing").status_code == 404


def test_city_fetch_cancel_marks_pending_layers():
    from app.server.core.city_fetch_tasks import CityFetchTask
    task = CityFetchTask(task_id="c", layers=["a", "b", "c"],
                         layer_states={"a": "done", "b": "fetching", "c": "pending"})
    task.cancel()
    assert task.layer_states == {"a": "done", "b": "cancelled", "c": "cancelled"}
    assert task.status == "cancelled"
