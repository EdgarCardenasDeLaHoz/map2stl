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

    def test_non_square_dem_succeeds(self, client):
        """F-PROJ-DIMS: projected DEMs can now be non-square (maintain_dimensions
        defaults to False) — the export pipeline must not assume height==width."""
        h, w = 6, 11
        body = {
            **_EXPORT_BODY,
            "dem_values": [float(i) for i in range(h * w)],
            "height": h, "width": w,
        }
        r = client.post("/api/export/stl", json=body)
        assert r.status_code == 200
        assert len(r.content) > 0

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
        deadline = time.monotonic() + 60.0
        while True:
            status = client.get(f"/api/export/status/{task_id}")
            assert status.status_code == 200
            status_payload = status.json()
            if status_payload["status"] == "complete":
                break
            assert status_payload["status"] == "running"
            if time.monotonic() > deadline:
                break
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


# ---------------------------------------------------------------------------
# PA-5: export checks the inline grid (size cap, value count) everywhere
# ---------------------------------------------------------------------------

class TestExportGridCheck:
    def test_check_dem_grid(self):
        import pytest

        from app.server.core.export_params import check_dem_grid
        check_dem_grid(_DEM_VALUES, _H, _W)                     # fine
        check_dem_grid([], 0, 0)                                # empty: caller's "missing"
        for vals, h, w in ([1.0] * 24, 5, 5),               \
                          ([1.0] * 6, 2.5, 2.4),            \
                          ([1.0] * 6, "a", 3),              \
                          ([1.0] * 6, -2, -3):
            with pytest.raises(ValueError):
                check_dem_grid(vals, h, w)
        with pytest.raises(ValueError, match="limit"):
            check_dem_grid([0.0] * 9, 3, 3, max_dim=2)

    def test_count_mismatch_is_400_on_every_sync_route(self, client):
        body = {**_EXPORT_BODY, "dem_values": _DEM_VALUES[:-1]}
        for route in ("stl", "obj", "3mf", "preview", "preflight"):
            r = client.post(f"/api/export/{route}", json=body)
            assert r.status_code == 400, route
            assert "expected" in r.json()["error"], route

    def test_oversized_grid_is_rejected(self, client, monkeypatch):
        import app.server.config as config
        monkeypatch.setattr(config, "MAX_DIM", 4)
        r = client.post("/api/export/stl", json=_EXPORT_BODY)        # 5 x 5 > 4
        assert r.status_code == 400 and "limit" in r.json()["error"]

    def test_async_start_rejects_bad_grid_before_queuing(self, client):
        body = {**_EXPORT_BODY, "dem_values": _DEM_VALUES[:-1], "format": "stl"}
        r = client.post("/api/export/start", json=body)
        assert r.status_code == 400 and "task_id" not in r.json()
        r = client.post("/api/export/puzzle", json={**_EXPORT_BODY, "dem_values": [1.0]})
        assert r.status_code == 400

    def test_crosssection_rejects_count_mismatch(self, client):
        body = {**_EXPORT_BODY, "dem_values": _DEM_VALUES[:-1],
                "north": 1.0, "south": 0.0, "east": 1.0, "west": 0.0}
        r = client.post("/api/export/crosssection", json=body)
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/export/crosssection
# ---------------------------------------------------------------------------

class TestCrossSection:
    """The slab is cut from the model's own heightfield (same z_mode and mm/px)."""

    _BODY = {**_EXPORT_BODY, "median_size": 0, "mm_per_pixel": 2.0, "thickness_mm": 4.0,
             "north": 0.01, "south": 0.0, "east": 0.01, "west": 0.0,
             "cut_axis": "lat", "cut_value": 0.005}

    def _slab(self, client, **over):
        import io

        import trimesh
        r = client.post("/api/export/crosssection", json={**self._BODY, **over})
        assert r.status_code == 200, r.text
        return trimesh.load(io.BytesIO(r.content), file_type="stl")

    def _model_row(self, **over):
        from app.server.core.export import _prepare_dem_array
        from app.server.core.export_params import ExportContext
        body = {**self._BODY, **over}
        body["bbox"] = {k: body[k] for k in ("north", "south", "east", "west")}
        im, *_ = _prepare_dem_array(ExportContext.from_request(body))
        return im[2, :]   # cut_value 0.005 is the middle row of 5

    def test_fit_mode_uses_the_whole_model_range(self, client):
        # The middle row spans 10..14 m of a 0..24 m DEM: it must not be stretched
        # to the full model height (the old per-row normalise made it 2..12 mm).
        mesh = self._slab(client, z_mode="fit")
        row = self._model_row(z_mode="fit")
        assert mesh.bounds[1][2] == pytest.approx(row.max(), abs=1e-3)
        assert mesh.bounds[1][2] < 2.0 + 10.0 - 1.0

    def test_true_scale_matches_the_model(self, client):
        mesh = self._slab(client, z_mode="true", exaggeration=3.0)
        row = self._model_row(z_mode="true", exaggeration=3.0)
        assert mesh.bounds[1][2] == pytest.approx(row.max(), abs=1e-3)

    def test_mm_per_pixel_scales_the_length(self, client):
        mesh = self._slab(client, z_mode="fit")
        assert mesh.extents[0] == pytest.approx((_W - 1) * 2.0, abs=1e-3)

    def test_default_base_is_5_mm(self, client):
        body = {k: v for k, v in self._BODY.items() if k != "base_height"}
        r = client.post("/api/export/crosssection", json={**body, "z_mode": "fit"})
        import io

        import trimesh
        mesh = trimesh.load(io.BytesIO(r.content), file_type="stl")
        assert mesh.bounds[1][2] == pytest.approx(self._model_row(z_mode="fit", base_height=5).max(),
                                                  abs=1e-3)
