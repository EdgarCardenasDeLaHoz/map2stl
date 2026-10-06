"""T41: the T38 height prior as T28's fallback for untagged buildings."""

import math

import numpy as np
import pytest
from shapely.geometry import Polygon

pytest.importorskip("sklearn")

from city2stl.skyline import benchmark as bm  # noqa: E402
from city2stl.skyline import untagged_prior as up  # noqa: E402
from city2stl.skyline._core.height import (  # noqa: E402
    UNTAGGED_FALLBACK_M,
    untagged_fallback,
    withhold_untagged_street_view,
)
from city2stl.skyline._core.types import BuildingRecord  # noqa: E402

LAT0, LON0 = 25.77, -80.19
KX = 111_320.0 * math.cos(math.radians(LAT0))


def _square(x, y, side):
    """lon/lat ring of a ``side`` m square at (x, y) m from the origin."""
    pts = [(x, y), (x + side, y), (x + side, y + side), (x, y + side), (x, y)]
    return [[LON0 + px / KX, LAT0 + py / 111_320.0] for px, py in pts]


def _rec(fid, x, y, side, source="default", tag=None):
    ring = _square(x, y, side)
    poly = Polygon(ring)
    c = poly.centroid
    return BuildingRecord(fid, fid, poly, c.y, c.x, tag, source, side * side)


def test_ring_metrics_of_a_square():
    area, perim, nv = up.ring_metrics(_square(0, 0, 20))
    assert area == pytest.approx(400, rel=0.01) and perim == pytest.approx(80, rel=0.01)
    assert nv == 4


def test_prior_reads_neighbour_tags_and_size():
    """Same small footprint: next to 150 m towers it reads taller than next to 10 m houses;
    a big footprint reads taller than a small one."""
    def scene(tag_m):
        recs = [_rec(f"t{i}", 60 * i - 120, 80, 30, "osm_tag", tag_m) for i in range(5)]
        return recs + [_rec("u", 0, 0, 12), _rec("big", 400, 0, 60)]
    low, high = (dict(zip([r.feature_id for r in s], up.predict(up.rows_from_records(s)),
                          strict=True)) for s in (scene(10.0), scene(150.0)))
    assert 2.0 < low["u"] < 40.0
    assert high["u"] > low["u"]
    assert low["big"] > low["u"]


def test_fallback_for_records_and_exclude_city():
    recs = [_rec("a", 0, 0, 12), _rec("b", 50, 0, 40), _rec("t", 0, 60, 30, "osm_tag", 90.0)]
    fb = up.fallback_for(recs, exclude_city="Miami")
    h, src = fb(recs[0])
    assert src == "prior_gbm" and 1.0 < h < 100.0
    assert fb(_rec("other", 0, 0, 10)) == (None, "prior_gbm")
    # leaving Miami out changes the model (it is one of the training cities)
    assert up._model("miami") is not up._model(None)


def test_untagged_fallback_switch(monkeypatch):
    recs = [_rec("u", 0, 0, 12)]
    monkeypatch.setenv("SKYLINE_UNTAGGED_FALLBACK", "constant")
    assert untagged_fallback(recs) is None
    monkeypatch.delenv("SKYLINE_UNTAGGED_FALLBACK")
    monkeypatch.delenv("SKYLINE_WITHHOLD_UNTAGGED", raising=False)
    rows = [{"feature_id": "u", "effective_height_m": 120.0,
             "effective_height_source": "geometric"}]
    withhold_untagged_street_view(rows, recs, fallback=untagged_fallback(recs, "miami"))
    assert rows[0]["effective_height_source"] == "withheld:prior_gbm"
    assert rows[0]["effective_height_m"] != UNTAGGED_FALLBACK_M
    assert rows[0]["street_view_m"] == 120.0


def test_benchmark_withheld_with_the_model():
    recs = [_rec("u", 0, 0, 12), _rec("t", 0, 60, 30, "osm_tag", 90.0)]
    report = [{"key": r.feature_id, "centroid_lat": r.centroid_lat,
               "centroid_lon": r.centroid_lon, "area_m2": r.area_m2,
               "footprint_lonlat": [list(p) for p in r.geometry.exterior.coords],
               "height_tag_m": r.height_tag_m, "height_source": r.height_source,
               "effective_height_m": 100.0} for r in recs]
    w = bm.withheld_buildings(report, "model", "miami")
    assert w[0]["effective_height_source"] == "withheld:prior_gbm"
    assert np.isfinite(w[0]["effective_height_m"]) and w[0]["street_view_m"] == 100.0
    assert w[1] is report[1]                                   # tagged: untouched
    c = bm.withheld_buildings(report)
    assert c[0]["effective_height_m"] == UNTAGGED_FALLBACK_M
