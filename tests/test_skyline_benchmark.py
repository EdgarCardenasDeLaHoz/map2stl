"""F-SKYBENCH: truth per footprint, cross-check and scorer on synthetic nDSMs (no network)."""

import json

import numpy as np
import pytest
from rasterio.transform import Affine
from shapely.geometry import box

from city2stl.skyline import benchmark as bm
from city2stl.skyline.region_types import RegionBBox

# A ~1.1 km square near Miami at ~1 m cells.
N, S, E, W = 25.780, 25.770, -80.180, -80.191
H = 1112
WD = 1104
T = Affine((E - W) / WD, 0.0, W, 0.0, -(N - S) / H, N)


def _cell_box(r0, r1, c0, c1):
    """Lon/lat box covering grid rows r0:r1, cols c0:c1."""
    x0, y0 = T @ (c0, r0)
    x1, y1 = T @ (c1, r1)
    return box(x0, y1, x1, y0)


def _ndsm(buildings):
    arr = np.zeros((H, WD), dtype=np.float32)
    for (r0, r1, c0, c1), h in buildings:
        arr[r0:r1, c0:c1] = h
    return arr


BUILDINGS = {
    "a": ((100, 140, 100, 140), 20.0),     # low-rise
    "b": ((400, 440, 400, 440), 45.0),     # mid-rise
    "c": ((800, 850, 800, 850), 150.0),    # tower
}


def _footprints():
    return [(k, _cell_box(*rc)) for k, (rc, _h) in BUILDINGS.items()]


def test_sample_p95_reads_inside_and_drops_edges():
    arr = _ndsm(BUILDINGS.values())
    poly = bm.inward_buffer(_cell_box(*BUILDINGS["c"][0]))
    v, n = bm.sample_p95(arr, T, poly)
    assert v == pytest.approx(150.0)
    assert n > 1000
    # A footprint over empty cells (all NaN) is unreadable, not zero.
    nan = np.full_like(arr, np.nan)
    assert bm.sample_p95(nan, T, poly)[0] is None


@pytest.mark.parametrize("s, t, status", [
    (100.0, 105.0, "confirmed"),      # within 10 %
    (20.0, 22.5, "confirmed"),        # within 3 m
    (100.0, 130.0, "disputed"),
    (50.0, None, "survey_only"),
    (None, 50.0, "tiles_only"),
    (None, None, "unread"),
])
def test_cross_check(s, t, status):
    assert bm.cross_check(s, t)[0] == status


def test_footprint_truth_cross_checks_two_sources_over_tiles():
    survey = _ndsm(BUILDINGS.values())
    tiles = _ndsm([(BUILDINGS["a"][0], 21.0), (BUILDINGS["b"][0], 70.0),
                   (BUILDINGS["c"][0], 155.0)])
    calls = []

    def fetcher(arr):
        def f(bbox):
            calls.append(bbox)
            return arr, T
        return f

    rows = bm.footprint_truth(_footprints(), (N, S, E, W), survey_fetch=fetcher(survey),
                              tiles_fetch=fetcher(tiles), tile_m=500.0)
    by = {r["key"]: r for r in rows}
    assert by["a"]["status"] == "confirmed" and by["a"]["truth_m"] == pytest.approx(20.0)
    assert by["b"]["status"] == "disputed" and by["b"]["truth_m"] is None
    assert by["c"]["status"] == "confirmed" and by["c"]["truth_m"] == pytest.approx(150.0)
    # Only tiles holding a centroid are fetched: one per building on the 3 x 3
    # diagonal, two sources each, of 9 tiles.
    assert len(calls) == 6


def test_tile_bboxes_cover_the_bbox():
    tiles = bm.tile_bboxes((N, S, E, W), 500.0)
    assert len(tiles) == 9
    assert max(t[0] for t in tiles) == pytest.approx(N)
    assert min(t[1] for t in tiles) == pytest.approx(S)
    assert max(t[2] for t in tiles) == pytest.approx(E)
    assert min(t[3] for t in tiles) == pytest.approx(W)


def _truth_doc():
    rows = bm.footprint_truth(_footprints(), (N, S, E, W),
                              survey_fetch=lambda b: (_ndsm(BUILDINGS.values()), T),
                              tiles_fetch=lambda b: (_ndsm(BUILDINGS.values()), T))
    return {"region": "synthetic", "counts": {"confirmed": len(rows)}, "rows": rows}


def _ring(poly):
    return [list(c) for c in poly.exterior.coords]


def test_score_region_joins_on_geometry_and_bands():
    # feature ids differ from truth keys: the join must be by footprint.
    fp = dict(_footprints())
    shifted = _cell_box(102, 142, 101, 141)          # same building, re-digitised
    heights = {"region": "synthetic", "buildings": [
        {"feature_id": "osm_9", "effective_height_m": 25.0, "n_views": 1, "n_seeds": 1,
         "height_tag_m": 21.0, "height_source": "osm_levels",
         "footprint_lonlat": _ring(shifted)},
        {"feature_id": "osm_7", "effective_height_m": 120.0, "n_views": 3, "n_seeds": 2,
         "height_tag_m": None, "height_source": "default",
         "footprint_lonlat": _ring(fp["c"])},
        {"feature_id": "nowhere", "effective_height_m": 50.0, "n_views": 1, "n_seeds": 1,
         "footprint_lonlat": _ring(_cell_box(900, 940, 900, 940))},
    ]}
    res = bm.score_region(heights, _truth_doc())
    o = res["overall"]
    assert o["n"] == 2
    assert o["mae_m"] == pytest.approx((5.0 + 30.0) / 2)
    assert o["bias_m"] == pytest.approx((5.0 - 30.0) / 2)
    assert res["by_band"][">100"]["bias_m"] == pytest.approx(-30.0)
    assert res["coverage"]["30-60"] == {"scored": 0, "truth": 1, "pct": 0.0}
    assert res["by_views"]["2+"]["n"] == 1
    assert res["osm_tags"]["tag_vs_truth"]["mae_m"] == pytest.approx(1.0)
    assert res["worst"][0]["feature_id"] == "osm_7"
    assert "synthetic" in bm.format_table([res])
    json.dumps(res)          # summary.json must serialise


def test_score_region_respects_statuses():
    doc = _truth_doc()
    for r in doc["rows"]:
        r["status"] = "survey_only"
    heights = {"buildings": [{"feature_id": "x", "effective_height_m": 20.0,
                              "footprint_lonlat": _ring(dict(_footprints())["a"])}]}
    assert bm.score_region(heights, doc)["overall"]["n"] == 0
    assert bm.score_region(heights, doc, statuses=("survey_only",))["overall"]["n"] == 1


def test_persisted_standoff_locations_round_trip(tmp_path, monkeypatch):
    from city2stl.skyline import seed_selection as ss
    from city2stl.skyline.region_types import SkylinePoint

    calls = []

    def fake(bbox, high_rises, osm_data, n_max=6):
        calls.append(1)
        return [SkylinePoint("auto_000_0600m", 25.77, -80.18, 90.0, "auto", 3.5)]

    monkeypatch.setattr(ss, "_propose_standoff_locations", fake)
    bbox = RegionBBox(name="miami", north=N, south=S, east=E, west=W)
    first = ss._persisted_standoff_locations("Miami", bbox, [], {}, proposals_dir=tmp_path)
    again = ss._persisted_standoff_locations("Miami", bbox, [], {}, proposals_dir=tmp_path)
    assert first == again and len(calls) == 1
    ss._persisted_standoff_locations("Miami", bbox, [], {}, proposals_dir=tmp_path, refresh=True)
    assert len(calls) == 2
    assert (tmp_path / "miami.json").exists()
