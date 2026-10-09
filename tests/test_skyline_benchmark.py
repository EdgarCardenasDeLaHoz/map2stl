"""F-SKYBENCH: truth per footprint and the scorer, on synthetic grids (no network)."""

import copy
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


def test_footprint_stat_needs_half_the_footprint_covered():
    # Survey coverage ends inside the building: only the west strip has data (Miami's coast).
    arr, t, _ = _grid()
    _paint(arr, 50, 50, 30, 20, 42.0)
    arr[:, 60:] = np.nan                        # east of x = 60 m: no survey
    poly = bm._polygon(_ring(50, 50, 30, 20))
    h, cells = bm.footprint_stat(arr, t, poly)
    assert h is None and cells > bm.MIN_CELLS   # enough cells, too small a share
    arr2, t2, _ = _grid()
    _paint(arr2, 50, 50, 30, 20, 42.0)
    arr2[:, 72:] = np.nan                       # ~70 % covered: accepted
    h2, _ = bm.footprint_stat(arr2, t2, poly)
    assert h2 == pytest.approx(42.0)


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


def test_tiles_are_a_fixed_world_grid_on_the_cell_lattice():
    from city2stl.height.providers._survey import lonlat_grid

    a = bm._polygon(_ring(0, 0, 10, 10))
    alone = bm.tiles_for({"a": a})
    crowd = bm.tiles_for({"a": a, "b": bm._polygon(_ring(-200, 150, 40, 40)),
                          "c": bm._polygon(_ring(250, -90, 20, 20))})
    tile_of_a = [t for t in crowd if "a" in t.keys]
    assert len(alone) == 1 and alone[0].bbox == tile_of_a[0].bbox   # same raster for "a"
    # exactly (tile + 2 pad) / res cells, so the cell size is the same fixed lattice
    h, wd, t = lonlat_grid(alone[0].bbox, bm.RESOLUTION_M)
    side = int((bm.TILE_M + 2 * bm.TILE_PAD_M) / bm.RESOLUTION_M)
    assert (h, wd) == (side, side)
    assert abs(t.e) * bm.M_PER_DEG_LAT == pytest.approx(bm.RESOLUTION_M, rel=1e-6)
    # a footprint too big for its tile gets its own bbox, covering it
    big = bm._polygon(_ring(-300, 0, 800, 50))
    own = bm.tiles_for({"big": big})
    n, s, e, w = own[0].bbox
    minx, miny, maxx, maxy = big.bounds
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


def _truth_fakes(monkeypatch, tmp_path, *, survey_fails=False, tiles_cover=True):
    monkeypatch.setattr(bm, "BENCHMARK_ROOT", tmp_path)
    survey, t, _ = _grid()
    _paint(survey, 20, 20, 30, 30, 50.0)
    calls = {"survey": 0, "tiles": 0}

    def fake_survey(provider, bbox, res):
        calls["survey"] += 1
        if survey_fails:
            raise RuntimeError("endpoint down")
        return survey, t

    def fake_tiles(bbox, res, provider):
        calls["tiles"] += 1
        return (survey, t) if tiles_cover else None

    monkeypatch.setattr(bm, "survey_ndsm", fake_survey)
    monkeypatch.setattr(bm, "tiles_ndsm", fake_tiles)
    return calls, {"A": _ring(20, 20, 30, 30)}


def test_no_tiles_read_is_scored_but_not_cached(tmp_path, monkeypatch):
    calls, fps = _truth_fakes(monkeypatch, tmp_path)
    first = bm.footprint_truth("Testville", fps, "usgs_3dep", use_tiles=False)
    assert first["A"]["status"] == "survey_only"
    assert bm.load_truth_cache("Testville") == {}
    full = bm.footprint_truth("Testville", fps, "usgs_3dep")   # a later full run measures again
    assert full["A"]["status"] == "confirmed" and calls == {"survey": 2, "tiles": 1}


def test_failed_source_is_not_cached(tmp_path, monkeypatch):
    calls, fps = _truth_fakes(monkeypatch, tmp_path, survey_fails=True)
    out = bm.footprint_truth("Testville", fps, "usgs_3dep")
    assert out["A"]["status"] == "tiles_only"
    assert bm.load_truth_cache("Testville") == {}


def test_not_covered_is_an_answer_and_cached(tmp_path, monkeypatch):
    calls, fps = _truth_fakes(monkeypatch, tmp_path, tiles_cover=False)
    out = bm.footprint_truth("Testville", fps, "usgs_3dep")
    assert out["A"]["status"] == "survey_only"
    assert "A" in bm.load_truth_cache("Testville")
    bm.footprint_truth("Testville", fps, "usgs_3dep")
    assert calls == {"survey": 1, "tiles": 1}


def test_refresh_remeasures_cached_footprints(tmp_path, monkeypatch):
    calls, fps = _truth_fakes(monkeypatch, tmp_path)
    bm.footprint_truth("Testville", fps, "usgs_3dep")
    bm.footprint_truth("Testville", fps, "usgs_3dep", refresh=True)
    assert calls == {"survey": 2, "tiles": 2}


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


# ── discover_city_seeds: city filter, keep existing site files (T7) ─────────

def _discover(monkeypatch, tmp_path):
    import importlib
    d = importlib.import_module("city2stl.skyline.scripts.discover_city_seeds")
    monkeypatch.setattr(d, "_SITES_DIR", tmp_path)
    monkeypatch.setattr(d, "_resolve_api_key", lambda *a, **k: "test-key")
    calls = []

    def meta(key, lat, lon, heading, radius_m=350):
        calls.append((lat, lon))
        return {"status": "OK", "location": {"lat": lat, "lng": lon}, "pano_id": "p1"}

    monkeypatch.setattr(d, "_streetview_metadata", meta)
    monkeypatch.setattr(d, "_geocode", lambda name, key: (40.0, -3.7))
    return d, calls


def test_discover_writes_only_the_named_cities(monkeypatch, tmp_path):
    d, calls = _discover(monkeypatch, tmp_path)
    city = next(iter(d.CITIES))
    d.main([city])
    assert [p.stem for p in tmp_path.glob("*.json")] == [city]
    assert calls                                          # metadata asked for that city


def test_discover_keeps_existing_site_files_unless_force(monkeypatch, tmp_path):
    import json
    d, calls = _discover(monkeypatch, tmp_path)
    city = next(iter(d.CITIES))
    site = tmp_path / f"{city}.json"
    site.write_text(json.dumps({"name": city, "anchor_offsets_deg": {"seed_1": 4.0}}))
    d.main([city])
    assert calls == []                                    # no Street View request at all
    assert json.loads(site.read_text())["anchor_offsets_deg"] == {"seed_1": 4.0}
    d.main([city, "--force"])
    assert calls and json.loads(site.read_text())["seed_urls"]


def test_discover_rejects_unknown_cities(monkeypatch, tmp_path):
    d, _ = _discover(monkeypatch, tmp_path)
    with pytest.raises(SystemExit):
        d.main(["atlantis"])


def _osm_region(monkeypatch, cache: dict, fetch):
    """Run ``_load_osm_for_region`` on a dict cache; returns (data, source, writes)."""
    from city2stl.skyline import region_data as rd
    from city2stl.skyline.region_types import RegionBBox

    writes = []
    monkeypatch.setattr(rd, "fetch_osm_data", fetch)
    monkeypatch.setattr(rd, "read_osm_cache",
                        lambda key, allow_stale=False: cache.get(key))
    monkeypatch.setattr(rd, "write_osm_cache",
                        lambda key, data: writes.append((key, copy.deepcopy(data))))
    bbox = RegionBBox("X", 1.0, 0.0, 1.0, 0.0)
    key = rd.osm_cache_key(bbox.north, bbox.south, bbox.east, bbox.west, 0.5, 5.0)
    data, source = rd._load_osm_for_region(bbox)
    return data, source, writes, key


def _fc(n=0):
    return {"type": "FeatureCollection", "features": [{"id": i} for i in range(n)]}


def test_cached_empty_waterways_is_fetched_again(monkeypatch):
    """T23: a waterways layer cached empty by a failed fetch is not taken as 'no water'."""
    from city2stl.skyline import region_data as rd

    asked = []

    def fetch(n, s, e, w, layers, **kw):
        asked.append(list(layers))
        return {layers[0]: _fc(3)}

    key = rd.osm_cache_key(1.0, 0.0, 1.0, 0.0, 0.5, 5.0)
    cache = {key: {"buildings": _fc(2), "green": _fc(1), "waterways": _fc(0)}}
    data, source, writes, _ = _osm_region(monkeypatch, cache, fetch)
    assert source.startswith("cache:")
    assert asked == [["waterways"]]                     # green had features: kept
    assert len(data["waterways"]["features"]) == 3
    assert writes and len(writes[-1][1]["waterways"]["features"]) == 3  # written back


def test_really_empty_layer_is_marked_and_not_fetched_again(monkeypatch):
    from city2stl.skyline import region_data as rd

    asked = []

    def fetch(n, s, e, w, layers, **kw):
        asked.append(list(layers))
        return {layers[0]: _fc(0)}                      # Overpass answered: no water here

    key = rd.osm_cache_key(1.0, 0.0, 1.0, 0.0, 0.5, 5.0)
    cache = {key: {"buildings": _fc(2), "green": _fc(1)}}
    _, _, writes, _ = _osm_region(monkeypatch, cache, fetch)
    assert asked == [["waterways"]]
    cache[key] = writes[-1][1]                          # next run reads the write-back
    asked.clear()
    _osm_region(monkeypatch, cache, fetch)
    assert asked == []


def test_failed_optional_fetch_stays_unmarked(monkeypatch):
    """A failed fetch leaves an empty, unmarked layer, so the next run tries again."""
    from city2stl.skyline import region_data as rd

    def fetch(n, s, e, w, layers, **kw):
        if layers == ["buildings"]:
            return {"buildings": _fc(2)}
        raise TimeoutError("ConnectTimeout")

    data, source, writes, key = _osm_region(monkeypatch, {}, fetch)
    assert source == "live_fetch"
    assert data["waterways"] == {"type": "FeatureCollection", "features": []}
    assert not rd._layer_present(writes[-1][1]["waterways"])


def test_stale_fallback_does_not_write_back(monkeypatch):
    """Writing a stale entry back would make it look fresh; the optional layer is filled in memory."""
    from city2stl.skyline import region_data as rd

    def fetch(n, s, e, w, layers, **kw):
        if layers == ["buildings"]:
            raise TimeoutError("mirror down")
        return {layers[0]: _fc(1)}

    key = rd.osm_cache_key(1.0, 0.0, 1.0, 0.0, 0.5, 5.0)
    stale = {"buildings": _fc(2), "green": _fc(1), "waterways": _fc(0)}

    def read(k, allow_stale=False):
        return stale if allow_stale and k == key else None

    monkeypatch.setattr(rd, "fetch_osm_data", fetch)
    monkeypatch.setattr(rd, "read_osm_cache", read)
    writes = []
    monkeypatch.setattr(rd, "write_osm_cache", lambda k, d: writes.append(k))
    from city2stl.skyline.region_types import RegionBBox
    data, source = rd._load_osm_for_region(RegionBBox("X", 1.0, 0.0, 1.0, 0.0))
    assert source.startswith("stale_cache:")
    assert len(data["waterways"]["features"]) == 1 and writes == []


class _KeylessTiles:
    """Google3DProvider without a key: ``covers()`` is False, as in the real provider."""
    _api_key = None

    def covers(self, bbox):
        return self._api_key is not None

    def fetch_heights(self, *a, **k):
        raise AssertionError("must not fetch without a key")


def test_tiles_without_a_key_is_a_failed_source(tmp_path, monkeypatch):
    """T36: a missing key used to read as "not covered", so a survey-only record was
    cached for good. Now it raises, the tile counts as incomplete and nothing is cached."""
    with pytest.raises(bm.TilesUnavailable):
        bm.tiles_ndsm((1.0, 0.0, 1.0, 0.0), provider=_KeylessTiles())

    monkeypatch.setattr(bm, "BENCHMARK_ROOT", tmp_path)
    survey, t, _ = _grid()
    _paint(survey, 20, 20, 30, 30, 50.0)
    monkeypatch.setattr(bm, "survey_ndsm", lambda provider, bbox, res: (survey, t))
    truth = bm.footprint_truth("Testville", {"A": _ring(20, 20, 30, 30)}, "usgs_3dep",
                               tiles_provider=_KeylessTiles())
    assert truth["A"]["status"] == "survey_only"            # scored this time
    assert bm.load_truth_cache("Testville") == {}            # but not pinned


def test_tiles_with_a_key_and_no_coverage_is_still_none():
    class Uncovered(_KeylessTiles):
        _api_key = "k"

        def covers(self, bbox):
            return False

    assert bm.tiles_ndsm((1.0, 0.0, 1.0, 0.0), provider=Uncovered()) is None


def test_a_published_height_goes_to_the_tower_not_the_podium_around_it():
    """Cartagena's Estelar: the convention-centre podium carries the hotel's name and is mapped
    around the unnamed tower; nearest-centroid matching scored the podium's 57 m against 202 m."""
    from city2stl.skyline.benchmark import match_known_tower

    def row(fid, name, tag, x0, y0, x1, y1):
        k = 1 / 111_320.0
        ring = [[x0 * k, y0 * k], [x1 * k, y0 * k], [x1 * k, y1 * k], [x0 * k, y1 * k], [x0 * k, y0 * k]]
        return {"feature_id": fid, "name": name, "height_tag_m": tag, "footprint_lonlat": ring,
                "centroid_lon": (x0 + x1) / 2 * k, "centroid_lat": (y0 + y1) / 2 * k}

    podium = row("p", "ESTELAR Hotel & Centro de Convenciones", None, -60, -40, 60, 40)
    tower = row("t", "", None, 20, 0, 50, 30)
    pt = (5 / 111_320.0, 0.0)                                 # published point: podium centre
    got, why = match_known_tower(pt[0], pt[1], 202.0, "Hotel Estelar Bocagrande", [podium, tower])
    assert got["feature_id"] == "t" and why == "nearest (not a podium)"   # untagged: not the podium
    tower["height_tag_m"] = 202.0
    got, why = match_known_tower(pt[0], pt[1], 202.0, "Hotel Estelar Bocagrande", [podium, tower])
    assert got["feature_id"] == "t" and why == "OSM tag"
    lone = row("x", "Estelar annex", None, 200, 0, 230, 30)  # a named building on its own
    got, why = match_known_tower(15 / 111_320.0, 230 / 111_320.0, 202.0,
                                 "Hotel Estelar Bocagrande", [lone, row("y", "", None, 240, 0, 260, 30)])
    assert got["feature_id"] == "x" and why == "name"


def test_the_report_loads_cartagenas_published_heights():
    """The loader looked for sites/ inside _region_render/ and silently found nothing."""
    from shapely.geometry import Point

    from city2stl.skyline._core.types import BuildingRecord
    from city2stl.skyline._region_render._pages import _load_known_heights

    rec = BuildingRecord("b1", "", Point(-75.55125, 10.40974).buffer(0.0001), 10.40974, -75.55125,
                         202.0, "osm_tag", 400.0)
    got = _load_known_heights("cartagena", [rec])
    assert len(got) >= 7
    assert any(k["matched_id"] == "b1" for k in got)

def test_flat_mesh_rule():
    tags = {"a": 150.0, "b": 200.0, "c": 125.0, "d": 12.0}
    assert bm.flat_mesh({"a": 9.0, "b": 15.0, "c": 4.0, "d": 11.0}, tags)       # towers read flat
    assert not bm.flat_mesh({"a": 148.0, "b": 15.0, "c": 120.0}, tags)          # mostly right
    assert not bm.flat_mesh({"a": 9.0, "b": 15.0}, tags)                        # too few towers
    assert not bm.flat_mesh({"a": 9.0, "b": 15.0, "c": 4.0}, {})                # no tags: no say


def test_flat_tiles_are_not_cached_as_truth(tmp_path, monkeypatch):
    """T40: a 3D Tiles area without a building mesh (Cartagena) reads every footprint near
    the ground. With the OSM tags, the tile is not covered: no tiles_only record."""
    monkeypatch.setattr(bm, "BENCHMARK_ROOT", tmp_path)
    flat, t, _ = _grid()
    for x in (20, 60, 100, 140):
        _paint(flat, x, 20, 25, 25, 8.0)          # towers tagged 150 m, tiles say 8 m
    monkeypatch.setattr(bm, "tiles_ndsm", lambda bbox, res, provider: (flat, t))
    fps = {k: _ring(x, 20, 25, 25) for k, x in zip("ABCD", (20, 60, 100, 140), strict=True)}
    tags = dict.fromkeys("ABC", 150.0)
    truth = bm.footprint_truth("Flatville", fps, None, tags_m=tags)
    assert all(r["status"] == "unmeasured" and r["tiles_m"] is None and r["tiles_flat"]
               for r in truth.values())
    cached = bm.load_truth_cache("Flatville")
    assert set(cached) == set("ABCD") and all(r["status"] == "unmeasured" for r in cached.values())


def test_cached_flat_records_are_dropped_without_new_reads(tmp_path, monkeypatch):
    """Records cached before T40 (tiles_only from a flat mesh) are dropped on the next
    scoring with tags, from the cache too, without fetching anything."""
    monkeypatch.setattr(bm, "BENCHMARK_ROOT", tmp_path)
    old = {k: {"survey_m": None, "survey_cells": 0, "tiles_m": v, "tiles_cells": 40,
               "status": "tiles_only", "truth_m": v}
           for k, v in zip("ABCD", (7.0, 12.0, 3.0, 9.0), strict=True)}
    bm.save_truth_cache("Flatville", old)

    def no_fetch(*a, **k):
        raise AssertionError("cached records must not be fetched again")

    monkeypatch.setattr(bm, "tiles_ndsm", no_fetch)
    monkeypatch.setattr(bm, "survey_ndsm", no_fetch)
    fps = {k: _ring(20, 20, 25, 25) for k in "ABCD"}
    truth = bm.footprint_truth("Flatville", fps, None, tags_m=dict.fromkeys("ABC", 180.0))
    assert {r["status"] for r in truth.values()} == {"unmeasured"}
    assert all(r["tiles_m"] is None for r in bm.load_truth_cache("Flatville").values())
    # without tags nothing changes: the rule needs towers to judge by
    bm.save_truth_cache("Flatville", old)
    assert bm.footprint_truth("Flatville", fps, None)["A"]["status"] == "tiles_only"


def test_real_mesh_is_kept_with_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "BENCHMARK_ROOT", tmp_path)
    tall, t, _ = _grid()
    for x in (20, 60, 100):
        _paint(tall, x, 20, 25, 25, 150.0)
    monkeypatch.setattr(bm, "tiles_ndsm", lambda bbox, res, provider: (tall, t))
    fps = {k: _ring(x, 20, 25, 25) for k, x in zip("ABC", (20, 60, 100), strict=True)}
    truth = bm.footprint_truth("Towerville", fps, None, tags_m=dict.fromkeys("ABC", 150.0))
    assert all(r["status"] == "tiles_only" for r in truth.values())


# --------------------------------------------------------------------------- truth age and keys


@pytest.mark.parametrize("start_date,year", [
    ("2017", 2017), ("2017-05-01", 2017), ("1920s", 1920), ("~1950", 1950),
    ("2016..2018", 2018), ("C19", None), (None, None), ("", None)])
def test_built_year(start_date, year):
    assert bm.built_year(start_date) == year


@pytest.mark.parametrize("start_date,years,flag", [
    ("2019", [2018, 2018], bm.TEMPORAL_MAY_POSTDATE),
    ("2018-11", [2018, 2018], bm.TEMPORAL_MAY_POSTDATE),    # the flight year: may postdate
    ("1987", [2018, 2018], bm.TEMPORAL_PREDATES),
    (None, [2018, 2018], bm.TEMPORAL_UNKNOWN),
    ("2019", None, bm.TEMPORAL_UNKNOWN)])
def test_temporal_flag(start_date, years, flag):
    assert bm.temporal_flag(start_date, years) == flag


def test_report_key_is_the_key_a_region_report_row_gets():
    # region_pdf writes footprint_lonlat at 6 decimals; truth measured on the OSM ring (7) must
    # be filed under the report's key, or no report row finds it (survey-only cities, 2026-10-09)
    ring = [[-66.06032169999999, 18.4321396], [-66.0601624, 18.431970999999997],
            [-66.0601, 18.4322], [-66.06032169999999, 18.4321396]]
    written = [[round(x, 6), round(y, 6)] for x, y in ring]
    assert bm.report_key(ring) == bm.footprint_key(written)
    assert bm.report_key(ring) != bm.footprint_key(ring)
