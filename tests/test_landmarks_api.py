"""Landmark endpoints (routers/cities.py, routers/regions.py) and the city task's
``landmark_overrides`` (core/landmarks.py, core/city_model_task.py)."""

import io
import json
import time
import zipfile

import numpy as np
import pytest
import trimesh
from test_landmarks import BBOX, BUILDINGS, CATHEDRAL, _fc


def _upload_box(client, name="box.stl", size=(8.0, 4.0, 2.0)):
    data = trimesh.creation.box(size).export(file_type="stl")
    r = client.post("/api/layers/mesh/upload", files={"file": (name, data, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()["upload_id"]


class TestList:
    def test_list_with_sources_and_overrides(self, client):
        client.put("/api/regions/TestRegion/landmarks/way/1", json={"kind": "ndsm", "provider": "auto"})
        r = client.post("/api/cities/landmarks",
                        json={"buildings": BUILDINGS, "tallest_n": 1, "region": "TestRegion"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert [i["osm_id"] for i in body["landmarks"]] == ["way/1", "way/4", "way/5"]
        names = {s["name"]: s["available"] for s in body["survey_sources"]}
        assert names["rediam_mdhn"] and names["cnig_mdsn"] and not names["ign_lidarhd"]
        assert body["overrides"] == {"way/1": {"kind": "ndsm", "provider": "auto"}}
        assert body["ids_missing"] is False

    def test_old_cache_without_ids(self, client):
        feats = [{"geometry": f["geometry"], "properties": {k: v for k, v in f["properties"].items()
                                                           if k != "osm_id"}}
                 for f in BUILDINGS["features"]]
        body = client.post("/api/cities/landmarks", json={"buildings": _fc(*feats)}).json()
        assert body["ids_missing"] is True and body["landmarks"][0]["osm_id"] is None

    def test_survey_sources(self, client):
        r = client.get("/api/cities/survey-sources",
                       params={"north": 48.86, "south": 48.85, "east": 2.36, "west": 2.34})
        got = {s["name"]: s["available"] for s in r.json()["sources"]}
        assert got["ign_lidarhd"] and not got["rediam_mdhn"]


class TestStorage:
    def test_crud(self, client):
        base = "/api/regions/TestRegion/landmarks"
        assert client.get(base).json()["overrides"] == {}
        r = client.put(f"{base}/way/1", json={"kind": "mesh", "upload_id": "abc", "rotation_deg": 90})
        assert r.status_code == 200 and r.json()["override"]["kind"] == "mesh"
        assert client.get(base).json()["overrides"]["way/1"]["rotation_deg"] == 90
        assert client.delete(f"{base}/way/1").status_code == 200
        assert client.delete(f"{base}/way/1").status_code == 404
        assert client.get(base).json()["overrides"] == {}

    @pytest.mark.parametrize("spec,status", [({"kind": "lego"}, 400), ({"kind": "mesh"}, 400),
                                             ({"kind": "ndsm", "provider": "x"}, 400)])
    def test_rejects_bad_specs(self, client, spec, status):
        assert client.put("/api/regions/TestRegion/landmarks/way/1", json=spec).status_code == status

    def test_unknown_region(self, client):
        r = client.put("/api/regions/Nowhere/landmarks/way/1", json={"kind": "osm"})
        assert r.status_code == 404


class TestPreview:
    def test_osm_preview(self, client):
        r = client.post("/api/cities/landmarks/preview", json={"buildings": BUILDINGS, "osm_id": "way/1"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["vertices"]) % 3 == 0 and len(body["faces"]) % 3 == 0
        assert body["report"]["watertight"] and body["report"]["landmarks"] == {}
        assert max(body["size_mm"][:2]) == pytest.approx(80.0, rel=0.1)

    def test_mesh_preview(self, client):
        uid = _upload_box(client)
        r = client.post("/api/cities/landmarks/preview", json={
            "buildings": BUILDINGS, "osm_id": "way/1",
            "override": {"kind": "mesh", "upload_id": uid, "vertical": "fit", "height_m": 20}})
        assert r.status_code == 200, r.text
        rep = r.json()["report"]["landmarks"]["way/1"]
        assert rep["status"] == "applied" and rep["replaced_features"] == 3

    def test_errors(self, client):
        r = client.post("/api/cities/landmarks/preview", json={"buildings": BUILDINGS, "osm_id": "way/99"})
        assert r.status_code == 400
        r = client.post("/api/cities/landmarks/preview", json={
            "buildings": BUILDINGS, "osm_id": "way/1", "override": {"kind": "mesh", "upload_id": "nope"}})
        assert r.status_code == 400 and "way/1" in r.json()["error"]

    def test_glb_upload_accepted(self, client):
        data = trimesh.creation.box((2, 2, 2)).export(file_type="glb")
        r = client.post("/api/layers/mesh/upload", files={"file": ("m.glb", data, "model/gltf-binary")})
        assert r.status_code == 200 and r.json()["format"] == "glb"


class TestCityTask:
    def _run(self, client, monkeypatch, overrides):
        import app.server.core.city_model_task as task_mod

        monkeypatch.setattr(task_mod, "get_city_layers", lambda *a, **k: {"buildings": _fc(CATHEDRAL)})
        dem = np.full((60, 60), 500.0)
        body = {"format": "city", "name": "t", "bbox": BBOX, "dem_values": dem.ravel().tolist(),
                "height": 60, "width": 60, "median_size": 0, "z_mode": "true",
                "layers": {"trails": {"enabled": False}}, "landmark_overrides": overrides}
        task_id = client.post("/api/export/start", json=body).json()["task_id"]
        for _ in range(300):
            st = client.get(f"/api/export/status/{task_id}").json()
            if st["status"] != "running":
                break
            time.sleep(0.2)
        return st, task_id

    def test_mesh_override_applied(self, client, monkeypatch):
        uid = _upload_box(client)
        st, task_id = self._run(client, monkeypatch, {"way/1": {"kind": "mesh", "upload_id": uid}})
        assert st["status"] == "complete", st
        z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/export/download/{task_id}").content))
        report = json.loads(z.read("report.json"))
        assert report["landmarks"]["way/1"]["status"] == "applied"
        assert trimesh.load(io.BytesIO(z.read("t.stl")), file_type="stl").is_watertight

    def test_bad_mesh_fails_the_task(self, client, monkeypatch):
        st, _ = self._run(client, monkeypatch, {"way/1": {"kind": "mesh", "upload_id": "missing"}})
        assert st["status"] == "error" and "way/1" in st.get("error", st.get("message", ""))
