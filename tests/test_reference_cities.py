"""Reference-city regression set (F-CITYMODEL): rebuild Cartagena, Granada + Alhambra and
Breckenridge end to end through the export task and check what prints.

Opt-in (``pytest -m slow tests/test_reference_cities.py``); marked ``integration`` too
because it reads the real disk cache (OSM, DEM, trails) and may fetch on a miss.
The model cache is off, so the geometry is rebuilt from the cached inputs every run.

Checks, per city:

- the export completes; the merged STL is watertight once welded and has positive volume;
- the puzzle (200 mm pieces) keeps >= 99 % of the model volume, every piece closed;
- faces and volume stay within tolerance of ``reference_cities_baseline.json``.

Every run appends faces, volume, time and the checks to
``output/regression/reference_cities.jsonl`` (build time is tracked, not asserted).
``MAP2STL_UPDATE_BASELINE=1`` rewrites the baseline from this run.
"""

from __future__ import annotations

import io
import json
import os
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import trimesh

pytestmark = [pytest.mark.slow, pytest.mark.integration]

REPO = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).with_name("reference_cities_baseline.json")
LOG = REPO / "output" / "regression" / "reference_cities.jsonl"
CITIES = {
    "cartagena": dict(north=10.4295, south=10.3845, east=-75.5221, west=-75.5679),
    "granada_alhambra": dict(north=37.1873, south=37.1693, east=-3.5780, west=-3.6093),
    "breckenridge": dict(north=39.51, south=39.43, east=-106.03, west=-106.125),
}
FACES_TOL = 0.15    # relative change in merged faces before the test fails
VOLUME_TOL = 0.03   # relative change in merged volume
MIN_KEPT = 0.99     # puzzle pieces / model volume, clearance gaps included
BUILD_TIMEOUT_S = 1800


def _build(client, name: str) -> tuple[dict, trimesh.Trimesh, float]:
    t0 = time.time()
    r = client.get("/api/terrain/dem", params={
        **CITIES[name], "dim": 1000, "dem_source": "SRTMGL1", "projection": "cosine",
        "depth_scale": 0.5, "water_scale": 0.05, "subtract_water": "true"})
    r.raise_for_status()
    body = {"format": "city", "dem_id": r.json()["dem_id"], "name": name, "mm_per_px": 1.0,
            "z_mode": "auto", "base_mm": 5.0, "median_size": 3,
            "puzzle": {"piece_mm": 200, "clearance_mm": 0.3}}
    task = client.post("/api/export/start", json=body).json()["task_id"]
    while True:
        st = client.get(f"/api/export/status/{task}").json()
        if st["status"] != "running":
            break
        if time.time() - t0 > BUILD_TIMEOUT_S:
            pytest.fail(f"{name}: build still running after {BUILD_TIMEOUT_S} s")
        time.sleep(1)
    assert st["status"] == "complete", f"{name}: {st}"
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/export/download/{task}").content))
    report = json.loads(z.read("report.json"))
    mesh = trimesh.load(io.BytesIO(z.read(f"{name}.stl")), file_type="stl")
    return report, mesh, time.time() - t0


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.server.server import app

    return TestClient(app)


@pytest.mark.parametrize("name", list(CITIES))
def test_reference_city(name, client, monkeypatch):
    monkeypatch.setenv("MAP2STL_CITY_CACHE", "0")
    # conftest sets test mode, under which /api/terrain/dem returns a synthetic gradient
    # and Earth Engine returns zeros: this set needs the real terrain.
    import app.server.config as config
    import app.server.routers.terrain as terrain_router

    monkeypatch.setenv("MAP2STL_TEST_MODE", "0")
    monkeypatch.setattr(config, "TEST_MODE", False)
    monkeypatch.setattr(terrain_router, "TEST_MODE", False)
    report, mesh, seconds = _build(client, name)
    vol = report["puzzle"]["volume"]
    run = {"city": name, "when": datetime.now(UTC).isoformat(timespec="seconds"),
           "seconds": round(seconds, 1), "size_mm": report["merged"]["size_mm"],
           "faces": len(mesh.faces),
           "volume_mm3": round(float(mesh.volume), 1), "watertight": bool(mesh.is_watertight),
           "puzzle_pieces": report["puzzle"]["pieces"], "puzzle_kept": vol["kept"],
           "open_pieces": vol["open_pieces"], "warnings": report["check"]["warnings"]}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(run) + "\n")

    assert run["watertight"], f"{name}: merged STL is not watertight"
    assert run["volume_mm3"] > 0
    assert vol["kept"] >= MIN_KEPT, f"{name}: puzzle keeps {vol['kept']:.2%} of the volume"
    assert not vol["open_pieces"], f"{name}: open puzzle pieces {vol['open_pieces']}"

    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    if os.environ.get("MAP2STL_UPDATE_BASELINE") == "1" or name not in base:
        base[name] = {k: run[k] for k in ("faces", "volume_mm3", "seconds", "puzzle_pieces")}
        BASELINE.write_text(json.dumps(base, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return
    ref = base[name]
    assert abs(run["faces"] / ref["faces"] - 1) <= FACES_TOL, \
        f"{name}: {run['faces']} faces vs baseline {ref['faces']}"
    assert abs(run["volume_mm3"] / ref["volume_mm3"] - 1) <= VOLUME_TOL, \
        f"{name}: volume {run['volume_mm3']} vs baseline {ref['volume_mm3']} mm3"
    assert run["puzzle_pieces"] == ref["puzzle_pieces"]
