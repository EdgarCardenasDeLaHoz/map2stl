"""Local usage log (app/server/routers/usage.py, F-USAGE)."""

import json

import pytest
from fastapi.testclient import TestClient

from app.server.server import app


@pytest.fixture
def usage_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MAP2STL_USAGE_DIR", str(tmp_path))
    monkeypatch.delenv("MAP2STL_USAGE_LOG", raising=False)
    return tmp_path


def _lines(d):
    (f,) = list(d.glob("*.jsonl"))
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]


def test_events_are_appended_with_the_session(usage_dir):
    c = TestClient(app)
    ev = [{"kind": "click", "id": "exportBtn", "label": "Export"},
          {"kind": "change", "id": "mmPerPx", "value": "1.0"}]
    assert c.post("/api/usage", json={"session": "s1", "events": ev}).json()["written"] == 2
    c.post("/api/usage", json={"session": "s2", "events": ev[:1]})
    rows = _lines(usage_dir)
    assert [r["session"] for r in rows] == ["s1", "s1", "s2"]
    assert rows[1]["value"] == "1.0"
    status = c.get("/api/usage/status").json()
    assert status["enabled"] and len(status["files"]) == 1


def test_secret_values_are_scrubbed(usage_dir):
    c = TestClient(app)
    ev = [{"kind": "change", "id": "opentopoApiKey", "value": "abc123"},
          {"kind": "change", "id": "x", "label": "Password", "value": "hunter2"},
          {"kind": "change", "id": "dim", "value": "1000"}]
    c.post("/api/usage", json={"session": "s", "events": ev})
    assert [r["value"] for r in _lines(usage_dir)] == ["[redacted]", "[redacted]", "1000"]


def test_bad_batches_are_refused_and_the_switch_disables(usage_dir, monkeypatch):
    c = TestClient(app)
    assert c.post("/api/usage", json={"events": "nope"}).status_code == 400
    assert c.post("/api/usage", content=b"not json").status_code == 400
    monkeypatch.setenv("MAP2STL_USAGE_LOG", "0")
    assert c.post("/api/usage", json={"events": [{"kind": "click"}]}).json()["written"] == 0
    assert not list(usage_dir.glob("*.jsonl"))
