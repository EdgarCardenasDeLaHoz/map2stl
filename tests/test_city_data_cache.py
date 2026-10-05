"""OSM layer cache reuse (app/server/core/city_data.lookup_city_layers).

A request whose exact (tolerance, min_area) entry is missing is served from a
finer entry for the same or an enclosing bbox - filtered, re-simplified and
clipped locally - instead of going to Overpass.
"""

import math
from unittest.mock import patch

import shapely
from shapely.geometry import shape

from app.server.core import city_data
from app.server.core.cache import osm_cache_key, read_osm_cache, write_osm_cache
from city2stl.cache_policy import CITY_PIPELINE_VERSION
from geo2stl.cache import list_osm_cache_params
from geo2stl.geo import M_PER_DEG_LAT

BBOX = dict(north=39.960, south=39.950, east=-75.140, west=-75.170)
LAT = 39.955
DLAT = 1 / M_PER_DEG_LAT                      # one metre, in degrees
DLON = 1 / (M_PER_DEG_LAT * math.cos(math.radians(LAT)))


def _building(x_m, y_m, w_m, h_m, jog_m=0.0, **props):
    """A w x h metre rectangle at (x, y) metres from the bbox's SW corner; ``jog_m``
    pushes a vertex out of the middle of its top edge (removed by simplification
    at a tolerance above it)."""
    x0, y0 = BBOX["west"] + x_m * DLON, BBOX["south"] + y_m * DLAT
    x1, y1 = x0 + w_m * DLON, y0 + h_m * DLAT
    ring = [[x0, y0], [x1, y0], [x1, y1], [(x0 + x1) / 2, y1 + jog_m * DLAT], [x0, y1], [x0, y0]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"height_m": 10.0, "height_source": "osm", **props}}


def _fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


def _payload(buildings, **layers):
    return {"buildings": _fc(*buildings), "roads": layers.get("roads", _fc()),
            "waterways": _fc(), "city_pipeline_version": CITY_PIPELINE_VERSION}


def _seed(payload, tol, min_area, bbox=BBOX, params=True):
    key = osm_cache_key(bbox["north"], bbox["south"], bbox["east"], bbox["west"], tol, min_area)
    write_osm_cache(key, payload, {**bbox, "tol": tol, "min_area": min_area} if params else None)
    return key


def _get(tol, min_area, bbox=BBOX, layers=("buildings", "roads", "waterways"), fetched=None):
    with patch("app.server.core.city_data.fetch_osm_data",
               return_value=fetched or {}) as fetch:
        out = city_data.get_city_layers(bbox["north"], bbox["south"], bbox["east"], bbox["west"],
                                        list(layers), tol, min_area)
    return out, fetch


class TestFinerEntryReuse:
    def test_finer_entry_is_filtered_and_resimplified_without_fetching(self, tmp_data_dir):
        big = _building(100, 100, 20, 10, jog_m=1.0)       # 200 m^2, 1 m jog
        small = _building(300, 300, 3, 3)                  # 9 m^2
        _seed(_payload([big, small]), tol=0.5, min_area=5.0)
        out, fetch = _get(3.0, 20.0)
        assert fetch.call_count == 0
        feats = out["buildings"]["features"]
        assert len(feats) == 1                             # 9 m^2 < 20 m^2 dropped
        # The 1 m jog is below the 3 m tolerance: the rectangle's 4 corners remain.
        assert len(shape(feats[0]["geometry"]).exterior.coords) == 5
        assert feats[0]["properties"]["height_m"] == 10.0

    def test_derived_entry_is_written_under_the_requested_key(self, tmp_data_dir):
        _seed(_payload([_building(100, 100, 20, 10)]), tol=0.5, min_area=5.0)
        _get(3.0, 20.0)
        key = osm_cache_key(*BBOX.values(), 3.0, 20.0)
        assert read_osm_cache(key)["buildings"]["features"]
        meta = {m["key"]: m for m in list_osm_cache_params()}[key]
        assert meta["tol"] == 3.0 and meta["min_area"] == 20.0 and meta["derived_from"]

    def test_coarser_entry_is_not_used(self, tmp_data_dir):
        _seed(_payload([_building(100, 100, 20, 10)]), tol=5.0, min_area=5.0)
        _, fetch = _get(3.0, 5.0, fetched=_payload([_building(100, 100, 20, 10)]))
        assert fetch.call_count == 1

    def test_larger_min_area_entry_is_not_used(self, tmp_data_dir):
        _seed(_payload([_building(100, 100, 20, 10)]), tol=0.5, min_area=50.0)
        _, fetch = _get(3.0, 5.0, fetched=_payload([]))
        assert fetch.call_count == 1

    def test_stale_candidate_is_not_used(self, tmp_data_dir):
        old = _payload([_building(100, 100, 20, 10)])
        old["city_pipeline_version"] = CITY_PIPELINE_VERSION - 2
        _seed(old, tol=0.5, min_area=5.0)
        _, fetch = _get(3.0, 5.0, fetched=_payload([]))
        assert fetch.call_count == 1

    def test_buildings_only_stale_candidate_supplies_the_other_layers(self, tmp_data_dir):
        from city2stl.cache_policy import BUILDINGS_ONLY_STALE_VERSIONS

        old = _payload([_building(100, 100, 20, 10)], roads=_fc(_building(0, 0, 5, 5)))
        old["city_pipeline_version"] = min(BUILDINGS_ONLY_STALE_VERSIONS)
        _seed(old, tol=0.5, min_area=5.0)
        fresh = {"buildings": _fc(_building(100, 100, 20, 10, name="fresh"))}
        out, fetch = _get(3.0, 5.0, fetched=fresh)
        assert fetch.call_count == 1 and fetch.call_args.args[4] == ["buildings"]
        assert out["buildings"]["features"][0]["properties"]["name"] == "fresh"
        assert out["roads"]["features"]

    def test_legacy_entry_without_params_is_found_by_probing(self, tmp_data_dir):
        key = _seed(_payload([_building(100, 100, 20, 10)]), tol=0.5, min_area=5.0, params=False)
        assert list_osm_cache_params() == []
        _, fetch = _get(3.0, 5.0)
        assert fetch.call_count == 0
        # ... and its params were recorded for the next lookup.
        assert key in {m["key"] for m in list_osm_cache_params()}

    def test_int_and_float_keys_are_the_same_settings(self, tmp_data_dir):
        _seed(_payload([_building(100, 100, 20, 10)]), tol=3, min_area=5, params=False)
        out, fetch = _get(3.0, 5.0)
        assert fetch.call_count == 0 and out["buildings"]["features"]

    def test_only_layers_missing_from_the_candidate_are_fetched(self, tmp_data_dir):
        payload = _payload([_building(100, 100, 20, 10)])
        del payload["roads"]
        _seed(payload, tol=0.5, min_area=5.0)
        out, fetch = _get(3.0, 5.0, fetched={"roads": _fc()})
        assert fetch.call_count == 1
        assert fetch.call_args.args[4] == ["roads"]
        assert out["buildings"]["features"]


class TestEnclosingBbox:
    def test_sub_bbox_is_clipped_from_a_larger_entry(self, tmp_data_dir):
        inside = _building(100, 100, 20, 10, name="inside")
        crossing = _building(1240, 100, 30, 10, name="crossing")   # straddles the east edge
        outside = _building(2000, 100, 20, 10, name="outside")
        _seed(_payload([inside, crossing, outside]), tol=0.5, min_area=5.0)
        sub = dict(north=BBOX["north"], south=BBOX["south"],
                   east=BBOX["west"] + 1250 * DLON, west=BBOX["west"])
        out, fetch = _get(0.5, 5.0, bbox=sub)
        assert fetch.call_count == 0
        names = sorted(f["properties"]["name"] for f in out["buildings"]["features"])
        assert names == ["crossing", "inside"]
        # A crossing feature is kept whole, as an Overpass bbox query returns it.
        got = [f for f in out["buildings"]["features"] if f["properties"]["name"] == "crossing"][0]
        assert shapely.equals(shape(got["geometry"]), shape(crossing["geometry"]))

    def test_exact_bbox_is_preferred_over_an_enclosing_one(self, tmp_data_dir):
        _seed(_payload([_building(100, 100, 20, 10, name="exact")]), tol=0.5, min_area=5.0)
        wider = dict(BBOX, east=BBOX["east"] + 0.01)
        _seed(_payload([_building(100, 100, 20, 10, name="wider")]), tol=1.0, min_area=5.0,
              bbox=wider)
        out, _ = _get(3.0, 5.0)
        assert out["buildings"]["features"][0]["properties"]["name"] == "exact"

    def test_a_bbox_not_covered_is_fetched(self, tmp_data_dir):
        _seed(_payload([_building(100, 100, 20, 10)]), tol=0.5, min_area=5.0)
        wider = dict(BBOX, east=BBOX["east"] + 0.01)
        _, fetch = _get(0.5, 5.0, bbox=wider, fetched=_payload([]))
        assert fetch.call_count == 1


class TestAnyToleranceRead:
    """Raster readers find the panel's 3.0 m entry (audit 2026-10-05: they read
    only the 0.5 m key, so the composite osm_* channels came back as zeros)."""

    def _read(self, bbox=BBOX, min_area=5.0):
        return city_data.read_city_layers_any_tolerance(
            bbox["north"], bbox["south"], bbox["east"], bbox["west"], min_area=min_area)

    def test_coarser_entry_is_found(self, tmp_data_dir):
        key = _seed(_payload([_building(100, 100, 20, 10, jog_m=1.0)]), tol=3.0, min_area=5.0)
        out, used = self._read()
        assert used == key
        assert len(out["buildings"]["features"]) == 1

    def test_enclosing_entry_is_clipped(self, tmp_data_dir):
        inner = dict(north=39.955, south=39.950, east=-75.155, west=-75.170)
        _seed(_payload([_building(100, 100, 20, 10), _building(2000, 900, 20, 10)]),
              tol=3.0, min_area=5.0)
        out, _ = self._read(bbox=inner)
        assert len(out["buildings"]["features"]) == 1

    def test_nothing_cached(self, tmp_data_dir):
        assert self._read() == ({}, None)
