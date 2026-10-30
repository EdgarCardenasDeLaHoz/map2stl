"""The Street View heights page API (routers/skyline.py, core/skyline_runs.py).

The library is monkeypatched: reports live in a temp folder, the pipeline is a stub, and nothing
reaches the network, the GPU or a real run.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from app.server.core import skyline_runs as sr
from app.server.routers import reports as reports_router
from geo2stl import google_usage as gu


def _building(i, tier, h=30.0, tag=None):
    x = -75.5 + i * 0.001
    return {"feature_id": f"w{i}", "name": f"B{i}", "effective_height_m": h,
            "effective_height_source": "street_view", "tier": tier, "n_seeds": 2,
            "height_tag_m": tag,
            "footprint_lonlat": [[x, 10.4], [x + 0.0005, 10.4], [x + 0.0005, 10.4005], [x, 10.4]]}


def _report(root, name, tiers):
    d = root / name
    d.mkdir(parents=True)
    bs = [_building(i, t) for i, t in enumerate(tiers)]
    (d / "heights.json").write_text(json.dumps({"region": "Cartagena", "buildings": bs}))
    (d / "index.html").write_text("<html></html>")
    return d


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "region_reports"
    r.mkdir()
    monkeypatch.setitem(reports_router._ROOTS, "region", r)
    _report(r, "Cartagena_v19_smoke_skyline_report", ["prior", "single"])
    _report(r, "Cartagena_v20_agree", ["corroborated", "corroborated", "tag", "prior"])
    _report(r, "miami_skyline_report", ["prior"])
    (r / "Cartagena_empty").mkdir()
    return r


def test_reports_for_matches_prefix_and_counts_tiers(root):
    rows = sr.reports_for("Cartagena")
    assert {r["dir"] for r in rows} == {"Cartagena_v19_smoke_skyline_report", "Cartagena_v20_agree"}
    v20 = next(r for r in rows if r["version"] == 20)
    assert v20["n_buildings"] == 4 and v20["tier_counts"]["corroborated"] == 2
    assert v20["title"] == "v20 agree"
    assert v20["index_url"].endswith("Cartagena_v20_agree/index.html")
    assert sr.next_report_name("Cartagena") == "Cartagena_v21_skyline_report"
    assert [r["dir"] for r in sr.reports_for("Miami")] == ["miami_skyline_report"]
    assert sr.reports_for("Nowhere") == []


def test_reports_endpoint_and_map(client, root):
    j = client.get("/api/skyline/regions/Cartagena/reports").json()
    assert len(j["reports"]) == 2 and j["next_report"] == "Cartagena_v21_skyline_report"
    m = client.get("/api/skyline/reports/Cartagena_v20_agree/map").json()
    assert len(m["buildings"]) == 4 and m["tiers"]["corroborated"]["label"]
    assert m["buildings"][0]["ring"][0] == [-75.5, 10.4]
    assert client.get("/api/skyline/reports/nope/map").status_code == 404
    assert client.get("/api/skyline/reports/..%2Fx/map").status_code in (400, 404)
    assert client.get("/api/skyline/regions/%3Cb%3E/reports").status_code == 400


def test_spin_params_match_what_the_pipeline_sends(monkeypatch, tmp_path):
    """The estimate looks images up under the same cache key the pipeline stores them under."""
    from city2stl.skyline import streetview_io as sv

    monkeypatch.setattr(sv, "_streetview_get", lambda url: (None, False))
    monkeypatch.setattr(sv, "_negative_cache_hit", lambda k: False)
    monkeypatch.setattr(sv, "_SV_IMAGE_CACHE_DIR", tmp_path / "img")
    keys = []
    real = sv.image_cache_key
    monkeypatch.setattr(sv, "image_cache_key", lambda p: keys.append(p) or real(p))
    w, h = sv._default_streetview_image_size()
    for pano in ("abc", None):
        keys.clear()
        sv._streetview_image("k", 1.5, 2.5, 30.0, fov=80.0, pitch=0.0, pano_id=pano,
                             pano_only=pano is not None, radius_m=200)
        mine = sr._spin_params(30.0, 0.0, 80.0, f"{w}x{h}", pano_id=pano, lat=1.5, lon=2.5)
        assert real(mine) == real({k: v for k, v in keys[0].items() if k != "key"})


def test_estimate_counts_cached_and_paid(monkeypatch):
    from city2stl.skyline import streetview_io as sv

    pts = [{"lat": 1, "lon": 2, "pitch": 0.0, "fov": 80.0, "pano_id": "a"},
           {"lat": 1, "lon": 2, "pitch": 0.0, "fov": 80.0, "pano_id": "a"},    # duplicate pano
           {"lat": 3, "lon": 4, "pitch": 0.0, "fov": 80.0, "pano_id": "b"}]
    monkeypatch.setattr(sr, "seed_points", lambda region: pts)
    monkeypatch.setattr(sv, "image_is_cached", lambda p: p["pano"] == "a" or p["heading"] < 60)
    gu.record("streetview_image", 7995)
    e = sr.estimate("X")
    assert (e["panoramas"], e["cached_panoramas"]) == (2, 1)
    assert e["images"] == 24 and e["paid_fetches"] == 10 and e["cached_images"] == 14
    assert e["month_count"] == 7995 and e["cap"] == 10_000
    assert e["over_warn"] is True            # 7995 + 10 > 8000


def test_estimate_unknown_region(client, monkeypatch):
    monkeypatch.setattr(sr, "seed_points", lambda region: [])
    e = client.get("/api/skyline/estimate", params={"region": "New"}).json()
    assert e["known"] is False and e["paid_fetches"] == 0 and "no saved viewpoints" in e["note"]


def _wait(client, tid, status, timeout=10):
    end = time.time() + timeout
    j = {}
    while time.time() < end:
        j = client.get(f"/api/skyline/runs/{tid}").json()
        if j["status"] == status:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job never reached {status}: {j}")


def test_run_is_cache_only_without_the_flag(client, root, monkeypatch):
    seen = {}

    def fake(region, out_pdf, *, skip_pdf, progress_cb):
        seen["paid"] = gu.paid_allowed()
        seen["skip_pdf"] = skip_pdf
        progress_cb("OSM fetch", None, None)
        progress_cb("Multiview registration (per-seed)", None, None)
        progress_cb("panoramas", 1, 4)
        d = out_pdf.parent / out_pdf.stem
        d.mkdir()
        (d / "heights.json").write_text(json.dumps({"buildings": []}))
        return {"region": region}

    monkeypatch.setattr("city2stl.skyline.region_pdf.run_region_pdf_report", fake)
    r = client.post("/api/skyline/runs", json={"region": "Cartagena"})
    assert r.status_code == 200
    j = _wait(client, r.json()["task_id"], "done")
    assert seen["paid"] is False and seen["skip_pdf"] is True
    assert j["report"] == "Cartagena_v21_skyline_report" and j["result"]["report"] == j["report"]
    assert (root / "Cartagena_v21_skyline_report" / "heights.json").is_file()
    assert gu.paid_allowed()                      # the switch is released afterwards


def test_run_with_flag_allows_paid(client, root, monkeypatch):
    seen = {}
    monkeypatch.setattr("city2stl.skyline.region_pdf.run_region_pdf_report",
                        lambda region, out, **k: seen.update(paid=gu.paid_allowed()) or {})
    r = client.post("/api/skyline/runs", json={"region": "Cartagena", "allow_paid_fetches": True})
    _wait(client, r.json()["task_id"], "done")
    assert seen["paid"] is True


def test_progress_and_cancel_stop_at_the_next_boundary(client, root, monkeypatch):
    go, reached, stopped = threading.Event(), threading.Event(), threading.Event()

    def fake(region, out, *, skip_pdf, progress_cb):
        progress_cb("OSM fetch", None, None)
        progress_cb("Multiview registration (per-seed)", None, None)
        progress_cb("panoramas", 3, 12)
        reached.set()
        go.wait(5)
        try:
            progress_cb("panoramas", 4, 12)       # raises once cancelled
        finally:
            stopped.set()
        raise AssertionError("ran past the cancel")

    monkeypatch.setattr("city2stl.skyline.region_pdf.run_region_pdf_report", fake)
    tid = client.post("/api/skyline/runs", json={"region": "Cartagena"}).json()["task_id"]
    assert reached.wait(5)
    j = client.get(f"/api/skyline/runs/{tid}").json()
    assert (j["step"], j["n_steps"], j["done"], j["total"]) == (3, 4, 3, 12)
    assert j["message"] == "3 / 12 panoramas" and j["step_label"] == "Detecting rooflines"
    assert j["gpu"] in ("in use", "waiting", "idle")
    assert client.post("/api/skyline/runs", json={"region": "Miami"}).status_code == 409
    assert client.post(f"/api/jobs/{tid}/cancel").json()["status"] == "cancelled"
    go.set()
    assert stopped.wait(5)
    assert client.get(f"/api/skyline/runs/{tid}").json()["status"] == "cancelled"
    time.sleep(0.3)
    assert gu.paid_allowed()


def test_usage_endpoints(client):
    gu.record("tiles_root", 3)
    gu.record("streetview_image", 5)
    g = client.get("/api/usage/google").json()
    assert g["kinds"]["tiles_root"]["count"] == 3
    assert g["kinds"]["streetview_image"]["cap"] == 10_000
    b = client.get("/api/benchmark/usage").json()
    assert b["value"] == 3 and b["limit"] == 1000


def test_reports_sort_by_version_and_old_reports_have_no_tier(client, root):
    """Newest version first whatever the file times; a report with no tier field is "No tier"."""
    d = root / "Cartagena_v7_old"
    d.mkdir()
    b = _building(0, "prior")
    del b["tier"]
    (d / "heights.json").write_text(json.dumps({"buildings": [b]}))
    assert [r["version"] for r in sr.reports_for("Cartagena")] == [20, 19, 7]
    j = client.get("/api/skyline/reports/Cartagena_v7_old/map").json()
    assert j["buildings"][0]["tier"] == "unlabelled"
    assert j["tier_counts"]["unlabelled"] == 1 and j["tiers"]["unlabelled"]["label"]


def test_estimate_floors_at_the_previous_reports_panoramas(root, monkeypatch):
    """Cartagena v20 used 5 distinct seeds; one saved viewpoint is cached, so the other 4
    count as paid (never understated), and the estimate says it is based on that report."""
    from city2stl.skyline import streetview_io as sv

    d = root / "Cartagena_v20_agree"
    bs = json.loads((d / "heights.json").read_text())
    for i, b in enumerate(bs["buildings"]):
        b["per_seed_median_m"] = {"seed_1": 30.0, f"auto_{i}": 31.0}
        b["views"] = [{"view_name": "seed_1_030"}, {"view_name": "seed_2_000"}]
    (d / "heights.json").write_text(json.dumps(bs))
    sr._seed_cache.clear()
    pts = [{"lat": 1, "lon": 2, "pitch": 0.0, "fov": 80.0, "pano_id": "a"}]
    monkeypatch.setattr(sr, "seed_points", lambda region: pts)
    monkeypatch.setattr(sr, "auto_points", lambda region: [])
    monkeypatch.setattr(sv, "image_is_cached", lambda p: True)
    e = sr.estimate("Cartagena")
    assert sr.report_panoramas("Cartagena") == (6, "Cartagena_v20_agree")   # seed_1, seed_2, 4 autos
    assert e["basis"] == "previous_report" and e["rough"] is False
    assert (e["panoramas"], e["cached_panoramas"], e["paid_fetches"]) == (6, 1, 60)
    assert "Cartagena_v20_agree" in e["note"]


def test_estimate_without_a_report_is_labelled_rough_and_counts_auto_points(monkeypatch):
    from city2stl.skyline import streetview_io as sv

    monkeypatch.setattr(sr, "seed_points", lambda region: [
        {"lat": 1, "lon": 2, "pitch": 0.0, "fov": 80.0, "pano_id": None}])
    monkeypatch.setattr(sr, "auto_points", lambda region: [
        {"lat": 1, "lon": 2, "pitch": 0.0, "fov": 80.0, "pano_id": None},      # same spot: once
        {"lat": 3, "lon": 4, "pitch": 0.0, "fov": 80.0, "pano_id": None}])
    monkeypatch.setattr(sv, "image_is_cached", lambda p: False)
    e = sr.estimate("Nowhere")
    assert e["basis"] == "seeds" and e["rough"] is True
    assert (e["panoramas"], e["paid_fetches"]) == (2, 24) and "lower bound" in e["note"]


def test_cache_only_run_does_not_block_a_paid_job_started_meanwhile(client, root, monkeypatch):
    """A cache-only Heights run is in flight; a paid job in another thread (a Benchmark score, say)
    still may spend, and the run itself stays cache-only the whole time."""
    go, reached, seen = threading.Event(), threading.Event(), {}

    def fake(region, out_pdf, *, skip_pdf, progress_cb):
        seen["before"] = gu.paid_allowed()
        reached.set()
        go.wait(5)
        seen["after"] = gu.paid_allowed()
        return {"region": region}

    monkeypatch.setattr("city2stl.skyline.region_pdf.run_region_pdf_report", fake)
    tid = client.post("/api/skyline/runs", json={"region": "Cartagena"}).json()["task_id"]
    assert reached.wait(5)
    other = []
    t = threading.Thread(target=lambda: other.append(gu.paid_allowed()))
    t.start()
    t.join(5)
    with gu.cache_only():                          # and a cache-only caller does not leak out
        pass
    go.set()
    _wait(client, tid, "done")
    assert other == [True]
    assert seen == {"before": False, "after": False}
    assert gu.paid_allowed()
