"""Export task status heartbeat (app/server/core/export_tasks.py).

The client polls ``/api/export/status`` and gives up only after a stall (no
status change and no heartbeat for 3 min), not after a fixed wall-clock time,
so the status must say whether the worker is still alive.
"""

import threading
import time

import app.server.core.export_tasks as et


def _register(task):
    with et._export_tasks_lock:
        et._export_tasks[task.task_id] = task


def _unregister(task):
    with et._export_tasks_lock:
        et._export_tasks.pop(task.task_id, None)


def test_status_reports_heartbeat_while_the_worker_runs():
    release = threading.Event()
    task = et.ExportTask(task_id="hb-alive")
    task.thread = threading.Thread(target=release.wait, daemon=True, name="export-hb-alive")
    task.thread.start()
    _register(task)
    try:
        st = et.get_task_status("hb-alive")
        assert st["status"] == "running"
        assert st["alive"] is True
        assert st["elapsed_s"] >= 0 and st["idle_s"] >= 0
    finally:
        release.set()
        task.thread.join(5)
    # The worker died without reporting: running, but no heartbeat.
    assert et.get_task_status("hb-alive")["alive"] is False
    _unregister(task)


def test_idle_resets_only_on_a_real_change():
    task = et.ExportTask(task_id="hb-idle")
    task.update(30, "Building model...")
    first = task.updated
    time.sleep(0.02)
    task.update(30, "Building model...")          # same status: not a change
    assert task.updated == first
    task.update(75, "Writing STL...")
    assert task.updated > first


def test_finished_task_is_not_alive_and_elapsed_stops():
    task = et.ExportTask(task_id="hb-done")
    task.fail("boom")
    _register(task)
    try:
        st = et.get_task_status("hb-done")
        assert st["alive"] is False
        elapsed = st["elapsed_s"]
        time.sleep(0.15)
        assert et.get_task_status("hb-done")["elapsed_s"] == elapsed
    finally:
        _unregister(task)


def test_status_route_includes_heartbeat(client):
    task = et.ExportTask(task_id="hb-route")
    _register(task)
    try:
        body = client.get("/api/export/status/hb-route").json()
        assert {"alive", "elapsed_s", "idle_s"} <= set(body)
        assert body["alive"] is True        # no thread recorded: trust the status
    finally:
        _unregister(task)
    assert client.get("/api/export/status/nope").status_code == 404
