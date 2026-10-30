"""The Plate align page's API (routers/plate.py, core/plate_align.py, city2stl/registration/align_edit.py).

A synthetic pack lives in a temp folder (ALIGN_DATA_DIR) and saved placements go to another
(ALIGN_GROUND_TRUTH_DIR). Nothing reaches the network, the real packs or the real ground truth.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pytest

from city2stl.registration import align_edit

SLUG = "testville_xx"
IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]


def _blocks(n=96):
    a = np.full((n, n), np.nan)
    for y0, x0 in ((10, 10), (10, 55), (55, 10), (55, 55)):
        a[y0:y0 + 28, x0:x0 + 28] = 12.0
    return a


@pytest.fixture
def pack(tmp_path, monkeypatch):
    data, gt = tmp_path / "data", tmp_path / "gt"
    folder = data / SLUG
    folder.mkdir(parents=True)
    arr = _blocks()
    np.save(folder / "stl_heightmap.npy", np.nan_to_num(arr, nan=0.0))
    np.save(folder / "osm_buildings.npy", arr)
    (folder / "stl_heightmap.png").write_bytes(b"x")
    (folder / "stl_mask.png").write_bytes(b"x")
    (folder / "osm_buildings.png").write_bytes(b"x")
    meta = {
        "slug": SLUG, "city": "Testville", "region": "XX", "resolution": 96, "cell_size_m": 5.0,
        "stl_shape": [96, 96], "osm_shape": [96, 96], "osm_bbox_nsew": [40.01, 40.0, -75.0, -75.01],
        "pipeline_guess": {"matrix": IDENTITY},
    }
    (folder / "meta.json").write_text(json.dumps(meta))
    # A surveyed pack has no plate to place: it must not be listed.
    sv = data / "surveyed_yy"
    sv.mkdir()
    (sv / "meta.json").write_text(json.dumps({**meta, "slug": "surveyed_yy", "pipeline_guess": None}))
    monkeypatch.setenv("ALIGN_DATA_DIR", str(data))
    monkeypatch.setenv("ALIGN_GROUND_TRUTH_DIR", str(gt))
    return {"data": data, "gt": gt}


def test_lists_only_placeable_plates(client, pack):
    r = client.get("/api/plate/plates")
    assert r.status_code == 200
    plates = r.json()["plates"]
    assert [p["slug"] for p in plates] == [SLUG]
    assert plates[0]["saved"] is False


def test_detail_has_shapes_images_and_guess(client, pack):
    d = client.get(f"/api/plate/plates/{SLUG}").json()
    assert d["stl_shape"] == [96, 96] and d["cell_size_m"] == 5.0
    assert d["guess"]["matrix"] == IDENTITY
    assert set(d["images"]) == {"stl", "stl_mask", "buildings"}
    assert d["saved"] is None


def test_unknown_or_unsafe_slug_is_404(client, pack):
    assert client.get("/api/plate/plates/nope").status_code == 404
    assert client.get("/api/plate/plates/..%2F..%2Fx").status_code == 404
    assert client.post("/api/plate/plates/nope/score", json={"matrix": IDENTITY}).status_code == 404


def test_image_only_known_names(client, pack):
    assert client.get(f"/api/plate/plates/{SLUG}/image/stl").status_code == 200
    assert client.get(f"/api/plate/plates/{SLUG}/image/satellite").status_code == 404
    assert client.get(f"/api/plate/plates/{SLUG}/image/meta.json").status_code == 404


def test_score_is_higher_for_the_right_placement(client, pack):
    good = client.post(f"/api/plate/plates/{SLUG}/score", json={"matrix": IDENTITY}).json()
    off = client.post(f"/api/plate/plates/{SLUG}/score",
                      json={"matrix": [[1, 0, 14], [0, 1, 9]]}).json()
    assert good["ok"] and good["iou"] > off["iou"]
    assert good["cell_size_m"] == 5.0
    assert good["edge_gap_m"] < off["edge_gap_m"]


def test_score_rejects_a_bad_matrix(client, pack):
    assert client.post(f"/api/plate/plates/{SLUG}/score", json={"matrix": [[1, 0, 0]]}).status_code == 422


def test_save_goes_to_the_redirected_folder_and_comes_back(client, pack):
    payload = {"transform": IDENTITY, "ui": {"flip": False}}
    r = client.post(f"/api/plate/plates/{SLUG}/save", json={"payload": payload}).json()
    assert r["ok"]
    assert (pack["gt"] / f"{SLUG}.json").is_file()
    assert json.loads((pack["gt"] / f"{SLUG}.json").read_text())["transform"] == IDENTITY
    d = client.get(f"/api/plate/plates/{SLUG}").json()
    assert d["saved"]["transform"] == IDENTITY
    assert client.get("/api/plate/plates").json()["plates"][0]["saved"] is True


def test_refine_runs_as_a_job(client, pack):
    j = client.post(f"/api/plate/plates/{SLUG}/refine", json={"matrix": IDENTITY}).json()
    for _ in range(100):
        j = client.get(f"/api/plate/jobs/{j['task_id']}").json()
        if j["status"] != "running":
            break
        time.sleep(0.1)
    assert j["status"] == "done"
    assert "improved" in j["result"] and len(j["result"]["matrix"]) == 2
    assert j["result"]["confidence"] is None  # NaN from ECC is sent as null, not as invalid JSON


def test_unknown_job_is_404(client):
    assert client.get("/api/plate/jobs/nope").status_code == 404


def test_standalone_tool_uses_the_same_save_and_load(pack):
    """tools/align_tool re-exports the shared functions, so both front ends write one format."""
    import importlib.util
    import sys

    from city2stl.registration.align_paths import ALIGN_TOOL
    sys.path.insert(0, str(ALIGN_TOOL))
    spec = importlib.util.spec_from_file_location("align_tool_server_t", ALIGN_TOOL / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.api_save is align_edit.api_save and mod.api_refine is align_edit.api_refine
    mod.api_save({"slug": SLUG, "payload": {"transform": IDENTITY}})
    assert align_edit.api_load({"slug": SLUG})["found"] is True
