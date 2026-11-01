"""The Benchmark page's API (routers/benchmark.py, core/benchmark_scores.py).

The library is monkeypatched: summaries come from a temp folder, scoring is a stub, and nothing
reaches the network or a real run.
"""

from __future__ import annotations

import json
import time

import pytest

from app.server.core import benchmark_scores as bs
from app.server.routers import benchmark as router_mod
from city2stl.skyline import benchmark_geometry as bm_geo


def _result(region, mae, n=40, **extra):
    base = {
        "region": region,
        "survey": "usgs_3dep_ept",
        "n_buildings": n + 5,
        "overall": {"n": n, "mae_m": mae, "median_ae_m": 2.0, "bias_m": 1.5, "within_25pct": 0.6},
        "street_view_unwithheld": {"overall": {"n": n}},
        "tiers": {
            "corroborated": {"n": 10, "mae_m": 2.0, "bias_m": 0.5, "within_25pct": 0.8},
            "prior": {"n": 20, "mae_m": 6.0, "bias_m": -1.0, "within_25pct": 0.4},
            "single": {"n": 0},
        },
    }
    base.update(extra)
    return base


def _write_summary(root, stamp, results):
    d = root / stamp
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps({"stamp": stamp, "results": results}))


@pytest.fixture
def bench(tmp_path, monkeypatch):
    root = tmp_path / "benchmark"
    monkeypatch.setattr(bm_geo, "BENCHMARK_ROOT", root)
    monkeypatch.setattr(bs.bm, "benchmark_runs", lambda: tmp_path / "local")
    monkeypatch.setattr(bs, "_RUNS", tmp_path)
    monkeypatch.setenv("MAP2STL_BENCHMARK_SCORES_DIR", str(tmp_path / "scores"))
    monkeypatch.setattr(bm_geo, "REGIONS", {"miami": "usgs_3dep_ept", "boston": "usgs_3dep_ept"})
    _write_summary(root, "2026-10-20_0900", [_result("miami", 9.0)])
    _write_summary(
        root, "2026-10-24_0900", [_result("miami", 5.5), {"region": "boston", "error": "no report"}]
    )
    return tmp_path


def test_regions_list_uses_newest_score_and_cartagena(client, bench):
    r = client.get("/api/benchmark/regions")
    assert r.status_code == 200
    regs = {x["id"]: x for x in r.json()["regions"]}
    assert regs["miami"]["mae_m"] == 5.5 and regs["miami"]["scored"] == "2026-10-24_0900"
    assert regs["miami"]["buildings"] == 40 and "USGS 3DEP lidar" in regs["miami"]["sub"]
    assert regs["boston"]["mae_m"] is None and "not scored" in regs["boston"]["sub"]
    assert regs["cartagena"]["mae_m"] is None and "weak truth" in regs["cartagena"]["sub"]


def test_region_detail_has_rows_and_delta(client, bench):
    d = client.get("/api/benchmark/regions/miami").json()
    assert d["current"]["mae_m"] == 5.5 and d["previous"]["mae_m"] == 9.0
    assert d["delta_mae_m"] == -3.5
    assert [r["key"] for r in d["current"]["rows"]] == ["corroborated", "prior"]
    assert d["current"]["within_kind"] == "25pct"
    assert d["heights_from"][0]["id"] == "latest"


def test_final_heights_pick_withheld_and_single_source():
    old = {
        "region": "x",
        "overall": {"n": 5, "mae_m": 50.0},
        "withheld_simulated": {"overall": {"n": 5, "mae_m": 12.0}},
    }
    assert bs._final(old)["mae_m"] == 12.0
    single = {
        "region": "x",
        "overall": {"n": 0},
        "single_source": {"overall": {"n": 3, "mae_m": 4.0}},
    }
    assert bs._final(single)["mae_m"] == 4.0
    assert bs._final({"region": "x", "error": "no report"}) is None


def test_unknown_region_404(client, bench):
    assert client.get("/api/benchmark/regions/nowhere").status_code == 404
    assert client.post("/api/benchmark/score", json={"region": "nowhere"}).status_code == 404


def test_usage_reads_the_google_counter(client):
    from geo2stl import google_usage

    d = client.get("/api/benchmark/usage").json()
    assert d["value"] == 0 and d["limit"] == 1000
    google_usage.record("tiles_root", 4)
    assert client.get("/api/benchmark/usage").json()["value"] == 4


def _wait(client, task_id, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{task_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.02)
    raise AssertionError("job did not finish")


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    router_mod._jobs.tasks.clear()


def _stub_report(bench, monkeypatch, missing, total=10, tiles=3):
    heights = bench / "region_reports" / "miami_skyline_report" / "heights.json"
    heights.parent.mkdir(parents=True)
    heights.write_text("{}")
    monkeypatch.setattr(
        bs,
        "truth_gap",
        lambda region, h: {"buildings": total, "missing": missing, "tile_fetches": tiles},
    )
    return heights


def test_score_cached_only_runs_job_and_stores_history(client, bench, monkeypatch):
    _stub_report(bench, monkeypatch, missing=2)
    calls = {}

    def fake(heights, region, **kw):
        calls.update(kw)
        return _result(region, 4.0, report=str(heights))

    monkeypatch.setattr(bs, "_score_report", fake)
    r = client.post("/api/benchmark/score", json={"region": "miami"})
    assert r.status_code == 200
    job = _wait(client, r.json()["task_id"])
    assert job["status"] == "done" and calls == {"cached_truth": True}
    d = client.get("/api/benchmark/regions/miami").json()
    assert d["current"]["mae_m"] == 4.0 and d["current"]["source"] == "job"
    assert d["previous"]["mae_m"] == 5.5 and d["delta_mae_m"] == -1.5
    assert "2 of 10 buildings have no cached truth" in d["current"]["note"]


def test_score_without_any_cached_truth_fails_with_fetch_count(client, bench, monkeypatch):
    _stub_report(bench, monkeypatch, missing=10, tiles=7)
    monkeypatch.setattr(bs, "_score_report", lambda *a, **k: pytest.fail("must not score"))
    job = _wait(
        client, client.post("/api/benchmark/score", json={"region": "miami"}).json()["task_id"]
    )
    assert job["status"] == "error"
    assert "7 3D Tiles fetches" in job["error"] and "Allow paid fetches" in job["error"]


def test_score_with_paid_fetches_allowed_uses_network_truth(client, bench, monkeypatch):
    _stub_report(bench, monkeypatch, missing=10)
    calls = {}
    monkeypatch.setattr(
        bs, "_score_report", lambda h, region, **kw: calls.update(kw) or _result(region, 3.0)
    )
    body = {"region": "miami", "allow_paid_fetches": True}
    job = _wait(client, client.post("/api/benchmark/score", json=body).json()["task_id"])
    assert job["status"] == "done" and calls == {"cached_truth": False}


def test_score_without_report_fails_cleanly(client, bench):
    job = _wait(
        client, client.post("/api/benchmark/score", json={"region": "miami"}).json()["task_id"]
    )
    assert job["status"] == "error" and "No height report" in job["error"]


def test_heights_from_cannot_leave_runs(bench):
    assert bs.resolve_heights("miami", "../../etc/heights.json") is None
