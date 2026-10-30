"""The Photo solve page's API (routers/photo.py, core/photo_solve.py over tools/photo_label).

A tiny region (a footprint file, no DEM, one photo) lives in a temp folder; the region table,
annotations and heights sidecar are redirected by environment variables. Nothing reaches the
network and no Google request can be made: the tests check that the usage counters stay put.
"""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from app.server.core import photo_solve as ps
from geo2stl import google_usage

RID = "testville"


def _jpeg(w=64, h=48) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 120, 160)).save(buf, "JPEG")
    return buf.getvalue()


def _footprints():
    feats = []
    for i, (lon, lat) in enumerate(((-3.5900, 37.1800), (-3.5895, 37.1800), (-3.5900, 37.1805))):
        d = 0.0002
        ring = [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]
        feats.append({"type": "Feature", "properties": {"id": 100 + i, "height": 9 + i, "name": f"B{i}"},
                      "geometry": {"type": "Polygon", "coordinates": [ring]}})
    return {"type": "FeatureCollection", "features": feats}


@pytest.fixture
def region(tmp_path, monkeypatch):
    osm = tmp_path / "buildings.geojson"
    osm.write_text(json.dumps(_footprints()))
    photos = tmp_path / "photos"
    (photos / "first").mkdir(parents=True)
    (photos / "first" / "photo.jpg").write_bytes(_jpeg())
    table = tmp_path / "regions.json"
    table.write_text(json.dumps({RID: {"label": "Testville", "osm": str(osm), "photos": {"": str(photos)},
                                       "view": {"lat": 37.18, "lon": -3.59}}}))
    ann = tmp_path / "ann"
    ann.mkdir()
    monkeypatch.setenv("MAP2STL_PHOTO_REGIONS_FILE", str(table))
    monkeypatch.setenv("MAP2STL_PHOTO_ANNOTATIONS_DIR", str(ann))
    ps.reset()
    before = google_usage.month_counts()
    yield {"dir": tmp_path, "ann": ann, "photos": photos}
    ps.reset()
    assert google_usage.month_counts() == before


def test_tool_is_the_standalone_module():
    t = ps.tool()
    assert t.__file__.replace("\\", "/").endswith("tools/photo_label/server.py")
    assert ps.tool() is t


def test_regions_and_photos(client, region):
    regs = client.get("/api/photo/regions").json()["regions"]
    assert regs[0]["id"] == RID and regs[0]["photos"] == 1 and regs[0]["view"]["lat"] == 37.18
    photos = client.get(f"/api/photo/regions/{RID}/photos").json()["photos"]
    assert len(photos) == 1 and photos[0]["pairs"] == 0 and photos[0]["solved"] is False


def test_unknown_region_and_photo_are_404(client, region):
    assert client.get("/api/photo/regions/nope/photos").status_code == 404
    assert client.get(f"/api/photo/regions/{RID}/image", params={"path": "../../etc/passwd"}).status_code == 404
    assert client.get(f"/api/photo/regions/{RID}/labels", params={"path": "missing/photo.jpg"}).status_code == 404


def test_image_is_served(client, region):
    path = client.get(f"/api/photo/regions/{RID}/photos").json()["photos"][0]["path"]
    r = client.get(f"/api/photo/regions/{RID}/image", params={"path": path})
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"


def test_upload_validation_and_listing(client, region):
    url = f"/api/photo/regions/{RID}/photos"
    assert client.post(url, params={"name": "notes.txt"}, content=b"hello").status_code == 400
    assert client.post(url, params={"name": "bad.jpg"}, content=b"not an image").status_code == 400
    assert client.post(url, params={"name": "empty.jpg"}, content=b"").status_code == 400
    ok = client.post(url, params={"name": "My View!.JPG"}, content=_jpeg(80, 60))
    assert ok.status_code == 200
    body = ok.json()
    assert body["w"] == 80 and body["h"] == 60 and "my_view" in body["path"]
    # same name again does not overwrite
    again = client.post(url, params={"name": "My View!.JPG"}, content=_jpeg()).json()
    assert again["path"] != body["path"]
    assert len(client.get(url).json()["photos"]) == 3


def test_labels_round_trip_and_status(client, region):
    path = client.get(f"/api/photo/regions/{RID}/photos").json()["photos"][0]["path"]
    body = {"photo": path, "image_px": [64, 48],
            "labels": [{"x": 5, "y": 6, "lat": 37.1801, "lon": -3.5899, "label": "A"}],
            "camera": {"lat": 37.17, "lon": -3.59, "rms_px": 3.2}, "camera_source": "solve"}
    r = client.post(f"/api/photo/regions/{RID}/labels", json=body)
    assert r.status_code == 200 and r.json()["n"] == 1
    got = client.get(f"/api/photo/regions/{RID}/labels", params={"path": path}).json()
    assert got["labels"][0]["label"] == "A"
    row = client.get(f"/api/photo/regions/{RID}/photos").json()["photos"][0]
    assert row["pairs"] == 1 and row["solved"] is True and row["camera_source"] == "solve"
    assert any(region["ann"].iterdir())


def test_flag_hides_a_photo_until_shown(client, region):
    path = client.get(f"/api/photo/regions/{RID}/photos").json()["photos"][0]["path"]
    r = client.post(f"/api/photo/regions/{RID}/flag", json={"photo": path, "bad": True, "reason": "fog"})
    assert r.status_code == 200
    rows = client.get(f"/api/photo/regions/{RID}/photos").json()["photos"]
    assert rows[0]["bad"] is True
    client.post(f"/api/photo/regions/{RID}/flag", json={"photo": path, "bad": False})
    assert not client.get(f"/api/photo/regions/{RID}/photos").json()["photos"][0].get("bad")
    assert client.post(f"/api/photo/regions/{RID}/flag", json={"photo": "zz/photo.jpg", "bad": True}).status_code == 404


def test_footprints_near_a_point_nearest_first_and_clamped(client, region):
    r = client.get(f"/api/photo/regions/{RID}/footprints", params={"lat": 37.1801, "lon": -3.5899, "radius_m": 300})
    j = r.json()
    assert j["total"] == 3 and j["features"][0]["ring"][0][0] < 0
    far = client.get(f"/api/photo/regions/{RID}/footprints",
                     params={"lat": 38.0, "lon": -3.5, "radius_m": 1}).json()
    assert far["features"] == []


def test_heights_are_kept_as_photo_source_in_the_sidecar(client, region):
    path = client.get(f"/api/photo/regions/{RID}/photos").json()["photos"][0]["path"]
    assert client.get(f"/api/photo/regions/{RID}/heights").json() == {}
    rows = [{"building": "100", "lat": 37.18, "lon": -3.59, "photo_m": 14.5, "osm_m": 9},
            {"building": "101", "lat": 37.18, "lon": -3.589, "photo_m": None, "osm_m": None}]
    r = client.post(f"/api/photo/regions/{RID}/heights", json={"photo": path, "rows": rows}).json()
    assert r["ok"] and r["saved"] == 1 and r["source"] == "photo"
    doc = client.get(f"/api/photo/regions/{RID}/heights").json()
    assert doc["source"] == "photo"
    assert doc["photos"][path]["heights"][0]["height_m"] == 14.5
    assert (region["ann"] / f"{RID}_photo_heights.json").is_file()
    assert client.post(f"/api/photo/regions/{RID}/heights", json={"photo": "zz/photo.jpg", "rows": rows}).status_code == 404


def test_commons_add_accepts_only_wikimedia_upload_urls(client, region):
    r = client.post(f"/api/photo/regions/{RID}/commons/add",
                    json={"url": "https://evil.example.com/a.jpg", "title": "File:A.jpg"})
    assert r.status_code == 400
    r = client.post(f"/api/photo/regions/{RID}/commons/add",
                    json={"url": "http://upload.wikimedia.org/a.jpg", "title": "File:A.jpg"})
    assert r.status_code == 400


def test_jobs_run_in_cache_only_scope(client, region, monkeypatch):
    """A register/commons job cannot send a Google request: the scope is cache-only inside the job."""
    seen = {}

    def fake(region_id, rel, refresh, progress):
        seen["only"] = not google_usage.paid_allowed()
        return {"ok": True, "candidates": []}

    monkeypatch.setattr(ps, "candidates", fake)
    path = client.get(f"/api/photo/regions/{RID}/photos").json()["photos"][0]["path"]
    j = client.post(f"/api/photo/regions/{RID}/register", json={"photo": path}).json()
    import time
    for _ in range(100):
        j = client.get(f"/api/photo/jobs/{j['task_id']}").json()
        if j["status"] != "running":
            break
        time.sleep(0.05)
    assert j["status"] == "done" and j["result"]["ok"] is True
    assert seen["only"] is True


def test_unknown_job_is_404(client):
    assert client.get("/api/photo/jobs/nope").status_code == 404
