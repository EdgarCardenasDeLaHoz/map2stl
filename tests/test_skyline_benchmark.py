"""F-SKYBENCH: truth per footprint and the scorer, on synthetic grids (no network)."""

import math

import numpy as np
import pytest
from rasterio.transform import from_bounds

from city2stl.skyline import benchmark as bm

LAT, LON = 40.0, -3.0
KX = bm.M_PER_DEG_LAT * math.cos(math.radians(LAT))


def _ring(x0_m, y0_m, w_m, h_m):
    """Lon/lat ring of a w×h metre rectangle whose SW corner is (x0, y0) m from (LON, LAT)."""
    x0, y0 = LON + x0_m / KX, LAT + y0_m / bm.M_PER_DEG_LAT
    x1, y1 = x0 + w_m / KX, y0 + h_m / bm.M_PER_DEG_LAT
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def _grid(size_m=200, res=1.0):
    """Zero nDSM of size_m × size_m at res, SW corner at (LON, LAT); returns (arr, transform, bbox)."""
    n = int(size_m / res)
    bbox = (LAT + size_m / bm.M_PER_DEG_LAT, LAT, LON + size_m / KX, LON)
    t = from_bounds(bbox[3], bbox[1], bbox[2], bbox[0], n, n)
    return np.zeros((n, n), np.float32), t, bbox


def _paint(arr, x0_m, y0_m, w_m, h_m, value, res=1.0):
    n = arr.shape[0]
    r0, r1 = n - int((y0_m + h_m) / res), n - int(y0_m / res)
    arr[r0:r1, int(x0_m / res):int((x0_m + w_m) / res)] = value


def test_footprint_stat_reads_roof_not_edges():
    arr, t, _ = _grid()
    _paint(arr, 50, 50, 30, 20, 42.0)
    _paint(arr, 80, 50, 5, 20, 120.0)  # a taller neighbour touching the east wall
    poly = bm._erode(bm._polygon(_ring(50, 50, 30, 20)), bm.ERODE_M)
    h, cells = bm.footprint_stat(arr, t, poly)
    assert h == pytest.approx(42.0)
    assert cells > 400


def test_footprint_stat_too_few_cells():
    arr, t, _ = _grid()
    arr[:] = np.nan
    h, cells = bm.footprint_stat(arr, t, bm._polygon(_ring(50, 50, 30, 20)))
    assert h is None and cells == 0


def test_erode_keeps_tiny_footprints():
    poly = bm._polygon(_ring(0, 0, 1.5, 1.5))
    assert bm._erode(poly, 1.0).equals(poly)


@pytest.mark.parametrize("s,t,status,truth", [
    (100.0, 105.0, "confirmed", 102.5),     # within 10 %
    (100.0, 115.0, "disputed", None),
    (10.0, 12.5, "confirmed", 11.25),       # within the 3 m floor
    (10.0, None, "survey_only", 10.0),
    (None, 20.0, "tiles_only", 20.0),
    (None, None, "unmeasured", None),
])
def test_classify(s, t, status, truth):
    got_status, got_truth = bm.classify(s, t)
    assert got_status == status
    assert got_truth == (None if truth is None else pytest.approx(truth))


def test_tiles_group_by_cell_and_cover_footprints():
    polys = {"a": bm._polygon(_ring(0, 0, 10, 10)), "b": bm._polygon(_ring(30, 0, 10, 10)),
             "c": bm._polygon(_ring(5000, 0, 10, 10))}
    tiles = bm.tiles_for(polys, tile_m=1000, pad_m=10)
    assert sorted(len(t.keys) for t in tiles) == [1, 2]
    for tile in tiles:
        n, s, e, w = tile.bbox
        for k in tile.keys:
            minx, miny, maxx, maxy = polys[k].bounds
            assert w < minx and maxx < e and s < miny and maxy < n


def test_footprint_truth_cross_checks_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "BENCHMARK_ROOT", tmp_path)
    survey, t, _ = _grid()
    _paint(survey, 20, 20, 30, 30, 50.0)     # A: both say 50
    _paint(survey, 100, 100, 30, 30, 80.0)   # B: tiles say 120 -> disputed
    tiles = survey.copy()
    _paint(tiles, 100, 100, 30, 30, 120.0)
    calls = {"survey": 0, "tiles": 0}

    def fake_survey(provider, bbox, res):
        calls["survey"] += 1
        return survey, t

    def fake_tiles(bbox, res, provider):
        calls["tiles"] += 1
        return tiles, t

    monkeypatch.setattr(bm, "survey_ndsm", fake_survey)
    monkeypatch.setattr(bm, "tiles_ndsm", fake_tiles)
    fps = {"A": _ring(20, 20, 30, 30), "B": _ring(100, 100, 30, 30)}
    truth = bm.footprint_truth("Testville", fps, "usgs_3dep")
    assert truth["A"]["status"] == "confirmed" and truth["A"]["truth_m"] == pytest.approx(50.0)
    assert truth["B"]["status"] == "disputed" and truth["B"]["truth_m"] is None
    assert calls == {"survey": 1, "tiles": 1}

    again = bm.footprint_truth("Testville", fps, "usgs_3dep")  # served from the cache
    assert again == truth and calls == {"survey": 1, "tiles": 1}


def test_score_buildings_bands_and_tags():
    truth = {"a": {"status": "confirmed", "truth_m": 20.0},
             "b": {"status": "confirmed", "truth_m": 150.0},
             "c": {"status": "disputed", "truth_m": None}}
    blds = [
        {"key": "a", "effective_height_m": 25.0, "n_views": 1, "n_seeds": 1,
         "height_tag_m": 17.0, "height_source": "osm_levels"},
        {"key": "b", "effective_height_m": 90.0, "n_views": 3, "n_seeds": 2,
         "height_tag_m": None, "height_source": "default"},
        {"key": "c", "effective_height_m": 40.0, "n_views": 1, "n_seeds": 1},
        {"key": "d", "effective_height_m": 40.0, "n_views": 1, "n_seeds": 1},
    ]
    s = bm.score_buildings(blds, truth)
    assert s["status"] == {"confirmed": 2, "disputed": 1, "unmeasured": 1}
    assert s["overall"]["n"] == 2
    assert s["overall"]["mae_m"] == pytest.approx(32.5)
    assert s["overall"]["bias_m"] == pytest.approx(-27.5)
    assert s["bands"]["0-30m"]["n"] == 1 and s["bands"]["100+m"]["bias_m"] == pytest.approx(-60.0)
    assert s["views"]["2-3"]["n"] == 1 and s["seeds"]["2+"]["n"] == 1
    assert s["osm_tag"]["tagged"]["n"] == 1 and s["osm_tag"]["untagged"]["n"] == 1
    assert s["osm_tags"]["tag_vs_truth"]["mae_m"] == pytest.approx(3.0)


def test_footprint_key_ignores_float_noise():
    ring = _ring(0, 0, 10, 10)
    noisy = [[x + 1e-10, y - 1e-10] for x, y in ring]
    assert bm.footprint_key(ring) == bm.footprint_key(noisy)


@pytest.mark.parametrize("name,key", [
    ("Miami, FL (2)", "miami"), ("miami", "miami"), ("Rio De Janeiro", "rio_de_janeiro"),
    ("La Defense", "la_defense"), ("Cartagena", "cartagena"),
])
def test_region_key(name, key):
    assert bm.region_key(name) == key


def test_auto_proposals_persist_per_region(tmp_path):
    from city2stl.skyline.region_types import SkylinePoint
    from city2stl.skyline.seed_selection import _persisted_proposals

    calls = []

    def propose():
        calls.append(1)
        return [SkylinePoint("auto_000_0900m", 25.7, -80.2, 12.5, "auto", 0.8, pano_id="abc")]

    first = _persisted_proposals("Miami", propose, tmp_path)
    again = _persisted_proposals("miami", propose, tmp_path)
    assert first == again and len(calls) == 1
    assert (tmp_path / "miami.json").exists()
    # an empty proposal set (failed OSM fetch) is not pinned
    assert _persisted_proposals("Nowhere", lambda: [], tmp_path) == []
    assert not (tmp_path / "nowhere.json").exists()


def _proposer(calls, lat=25.7):
    from city2stl.skyline.region_types import SkylinePoint

    def propose():
        calls.append(1)
        return [SkylinePoint("auto_000_0900m", lat, -80.2, 12.5, "auto", 0.8)]
    return propose


def test_auto_proposals_recompute_when_the_bbox_changes(tmp_path):
    from city2stl.skyline.region_types import RegionBBox
    from city2stl.skyline.seed_selection import _persisted_proposals

    calls = []
    small = RegionBBox("miami", 25.80, 25.75, -80.15, -80.20)
    big = RegionBBox("miami", 25.85, 25.70, -80.10, -80.25)
    a = _persisted_proposals("miami", _proposer(calls), tmp_path, bbox=small)
    b = _persisted_proposals("miami", _proposer(calls), tmp_path, bbox=small)
    assert a == b and len(calls) == 1                     # same bbox: reused
    c = _persisted_proposals("miami", _proposer(calls, lat=25.71), tmp_path, bbox=big)
    assert len(calls) == 2 and c[0].lat == 25.71          # bbox changed: proposed afresh
    d = _persisted_proposals("miami", _proposer(calls), tmp_path, bbox=big)
    assert d == c and len(calls) == 2                     # and saved for the new bbox


def test_legacy_proposal_file_is_kept_and_upgraded(tmp_path):
    import json

    from city2stl.skyline.region_types import RegionBBox
    from city2stl.skyline.seed_selection import _persisted_proposals

    (tmp_path / "miami.json").write_text(json.dumps([
        {"name": "auto_090_0600m", "lat": 25.76, "lon": -80.18, "heading": 270.0,
         "source": "auto", "score": 1.0}]), encoding="utf-8")
    calls = []
    bbox = RegionBBox("miami", 25.80, 25.75, -80.15, -80.20)
    pts = _persisted_proposals("miami", _proposer(calls), tmp_path, bbox=bbox)
    assert calls == [] and pts[0].name == "auto_090_0600m"   # baseline cameras kept
    doc = json.loads((tmp_path / "miami.json").read_text(encoding="utf-8"))
    assert doc["bbox_nsew"] == [25.8, 25.75, -80.15, -80.2] and len(doc["points"]) == 1


def test_failed_recompute_keeps_the_old_file(tmp_path):
    from city2stl.skyline.region_types import RegionBBox
    from city2stl.skyline.seed_selection import _persisted_proposals

    calls = []
    old = RegionBBox("miami", 25.80, 25.75, -80.15, -80.20)
    _persisted_proposals("miami", _proposer(calls), tmp_path, bbox=old)
    new = RegionBBox("miami", 25.85, 25.70, -80.10, -80.25)
    assert _persisted_proposals("miami", lambda: [], tmp_path, bbox=new) == []
    assert _persisted_proposals("miami", _proposer(calls), tmp_path, bbox=old) != []
    assert len(calls) == 1                                # the old bbox's set survived


def test_osm_load_survives_a_waterways_timeout(monkeypatch):
    """Buildings are the only required OSM layer; a waterways timeout must not fail the run."""
    from city2stl.skyline import region_data as rd
    from city2stl.skyline.region_types import RegionBBox

    asked = []

    def fake_fetch(n, s, e, w, layers, **kw):
        asked.append(list(layers))
        if layers == ["buildings"]:
            return {"buildings": {"type": "FeatureCollection", "features": [{"id": 1}]}}
        raise TimeoutError("ConnectTimeout")

    monkeypatch.setattr(rd, "fetch_osm_data", fake_fetch)
    monkeypatch.setattr(rd, "read_osm_cache", lambda key, allow_stale=False: None)
    monkeypatch.setattr(rd, "write_osm_cache", lambda key, data: None)
    data, source = rd._load_osm_for_region(RegionBBox("X", 1.0, 0.0, 1.0, 0.0))
    assert source == "live_fetch"
    assert data["buildings"]["features"] and data["waterways"]["features"] == []
    assert asked == [["buildings"], ["waterways"]]  # roads are no longer requested


def test_relative_metrics_ignore_offset_and_scale():
    truth = np.array([10.0, 40.0, 90.0, 150.0])
    pred = truth * 0.5 + 30.0          # wrong scale and offset, right order
    r = bm.relative_metrics(pred, truth)
    assert r["pair_order"] == 1.0 and r["spearman"] == pytest.approx(1.0)
    flipped = bm.relative_metrics(truth[::-1].copy(), truth)
    assert flipped["pair_order"] == 0.0


def test_pairs_skip_near_equal_truth():
    ok, n = bm._pairs(np.array([5.0, 9.0, 100.0]), np.array([20.0, 21.0, 80.0]))
    assert n == 2 and ok == 2           # 20 vs 21 m is inside the truth tolerance


def test_per_view_relative_pools_views():
    truth = {k: {"status": "confirmed", "truth_m": h}
             for k, h in zip("abcdef", (10.0, 20.0, 40.0, 80.0, 120.0, 160.0), strict=True)}
    good = [("a", 1), ("b", 2), ("c", 3), ("d", 4), ("e", 5)]
    blds = [{"key": k, "views": [{"view_name": "v1", "height_m": h}]} for k, h in good]
    blds.append({"key": "f", "views": [{"view_name": "v2", "height_m": 1.0}]})  # lone view: skipped
    r = bm.per_view_relative(blds, truth)
    assert r["views"] == 1 and r["pair_order"] == 1.0 and r["pairs"] == 10


# ── 10_benchmark: pinned flags (F-SKYBENCH port, T3) ────────────────────────

def _bench_script():
    import importlib
    return importlib.import_module("city2stl.skyline.scripts.10_benchmark")


def test_region_env_pins_flags_over_the_shell():
    s = _bench_script()
    env = s._region_env(base={"SKYLINE_TAG_FILTER": "0", "SKYLINE_CV_SEGFORMER_SIZE": "b3",
                              "PATH": "/bin"})
    assert env["SKYLINE_TAG_FILTER"] == "1"                 # baseline value, not the shell's
    assert env["SKYLINE_CV_SEGFORMER_SIZE"] == "b1"
    assert env["SKYLINE_CV_SEGFORMER_INPUT_SIZE"] == "512"
    assert env["PATH"] == "/bin" and env["PYTHONIOENCODING"] == "utf-8"


def test_keep_env_lets_shell_values_through():
    s = _bench_script()
    env = s._region_env(keep_env=True, base={"SKYLINE_TAG_FILTER": "0"})
    assert env["SKYLINE_TAG_FILTER"] == "0"
    assert "SKYLINE_CV_SEGFORMER_SIZE" not in env


def test_pinned_flags_are_flags_the_pipeline_reads():
    # A renamed or removed flag would silently stop being pinned.
    from pathlib import Path

    src = "".join(p.read_text(encoding="utf-8")
                  for p in Path(bm.__file__).parent.rglob("*.py") if "scripts" not in p.parts)
    for flag in _bench_script().PINNED_FLAGS:
        assert f'"{flag}"' in src, flag


def test_run_settings_record_flags_and_signing():
    s = _bench_script()
    rec = s._run_settings({**s._region_env(base={}), "GOOGLE_MAPS_SIGN_SECRET": "x"})
    assert rec["signed_streetview"] is True
    assert rec["env"]["SKYLINE_TAG_FILTER"] == "1"
    assert "PYTHONIOENCODING" not in rec["env"] and "GOOGLE_MAPS_SIGN_SECRET" not in rec["env"]
    assert s._run_settings(s._region_env(base={}))["signed_streetview"] is False
