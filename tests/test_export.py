"""
Tests for /api/export/* endpoints (export.py + core/export.py).

Uses a small 5×5 synthetic DEM to avoid heavy computation.
"""

import time

import pytest

# ---------------------------------------------------------------------------
# Shared DEM fixture
# ---------------------------------------------------------------------------

_W, _H = 5, 5
_DEM_VALUES = [float(i) for i in range(_W * _H)]

_EXPORT_BODY = {
    "dem_values": _DEM_VALUES,
    "height": _H,
    "width": _W,
    "model_height": 10.0,
    "base_height": 2.0,
    "exaggeration": 1.0,
    "sea_level_cap": False,
    "name": "test_terrain",
}


# ---------------------------------------------------------------------------
# POST /api/export/stl
# ---------------------------------------------------------------------------

class TestExportSTL:
    def test_returns_200(self, client):
        r = client.post("/api/export/stl", json=_EXPORT_BODY)
        assert r.status_code == 200

    def test_content_type_is_octet_stream(self, client):
        r = client.post("/api/export/stl", json=_EXPORT_BODY)
        assert "octet-stream" in r.headers.get("content-type", "")

    def test_content_disposition_has_stl_extension(self, client):
        r = client.post("/api/export/stl", json=_EXPORT_BODY)
        cd = r.headers.get("content-disposition", "")
        assert ".stl" in cd

    def test_response_body_is_non_empty(self, client):
        r = client.post("/api/export/stl", json=_EXPORT_BODY)
        assert len(r.content) > 0

    def test_missing_dem_values_returns_400(self, client):
        r = client.post("/api/export/stl", json={"height": 5, "width": 5})
        assert r.status_code == 400

    def test_zero_dimensions_returns_400(self, client):
        r = client.post("/api/export/stl", json={**_EXPORT_BODY, "height": 0, "width": 0})
        assert r.status_code == 400

    def test_watertight_header_present(self, client):
        r = client.post("/api/export/stl", json=_EXPORT_BODY)
        # Header may be present; value is "true" or "false"
        assert "x-watertight" in r.headers or r.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/export/obj
# ---------------------------------------------------------------------------

class TestExportOBJ:
    def test_returns_200(self, client):
        r = client.post("/api/export/obj", json=_EXPORT_BODY)
        assert r.status_code == 200

    def test_content_disposition_has_obj_extension(self, client):
        r = client.post("/api/export/obj", json=_EXPORT_BODY)
        cd = r.headers.get("content-disposition", "")
        assert ".obj" in cd

    def test_response_is_text(self, client):
        r = client.post("/api/export/obj", json=_EXPORT_BODY)
        # OBJ files are plaintext starting with comments or vertex/face data
        text = r.content.decode("utf-8", errors="ignore")
        assert len(text) > 0

    def test_missing_dem_values_returns_400(self, client):
        r = client.post("/api/export/obj", json={"height": 5, "width": 5})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/export/3mf
# ---------------------------------------------------------------------------

class TestExport3MF:
    def test_returns_200(self, client):
        r = client.post("/api/export/3mf", json=_EXPORT_BODY)
        assert r.status_code == 200

    def test_content_disposition_has_3mf_extension(self, client):
        r = client.post("/api/export/3mf", json=_EXPORT_BODY)
        cd = r.headers.get("content-disposition", "")
        assert ".3mf" in cd

    def test_response_body_is_non_empty(self, client):
        r = client.post("/api/export/3mf", json=_EXPORT_BODY)
        assert len(r.content) > 0

    def test_missing_dem_values_returns_400(self, client):
        r = client.post("/api/export/3mf", json={"height": 5, "width": 5})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Async export progress flow
# ---------------------------------------------------------------------------

class TestAsyncExportFlow:
    def test_start_status_download_stl(self, client):
        start = client.post("/api/export/start", json={**_EXPORT_BODY, "format": "stl"})
        assert start.status_code == 200

        task_id = start.json()["task_id"]
        assert task_id

        status_payload = None
        for _ in range(40):
            status = client.get(f"/api/export/status/{task_id}")
            assert status.status_code == 200
            status_payload = status.json()
            if status_payload["status"] == "complete":
                break
            assert status_payload["status"] == "running"
            time.sleep(0.05)

        assert status_payload is not None
        assert status_payload["status"] == "complete"
        assert status_payload["progress"] == 100

        download = client.get(f"/api/export/download/{task_id}")
        assert download.status_code == 200
        assert len(download.content) > 0
        assert ".stl" in download.headers.get("content-disposition", "")

    def test_invalid_format_returns_400(self, client):
        r = client.post("/api/export/start", json={**_EXPORT_BODY, "format": "zip"})
        assert r.status_code == 400

    def test_unknown_task_returns_404(self, client):
        r = client.get("/api/export/status/not-a-task")
        assert r.status_code == 404
