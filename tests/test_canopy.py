"""city2stl.canopy: tree-canopy mask, heights, solids and the ``trees`` city-model layer."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh
from rasterio.transform import Affine

from city2stl import canopy as cn
from city2stl.city_model import (
    Terrain,
    build_on_terrain,
    choose_scale,
    welded_watertight,
)
from city2stl.height.providers._survey import M_PER_DEG_LAT, lonlat_grid

BBOX = dict(north=37.1840, south=37.1800, east=-3.5980, west=-3.6040)
MM_PER_M = 0.2


def _scale(dem_shape=(40, 60)):
    # 10 m DEM cells at 0.2 mm per metre -> 2 mm per DEM pixel
    return choose_scale(BBOX, dem_shape, 100.0, 110.0, mm_per_px=2.0)


def _dem():
    return np.full((40, 60), 100.0) + np.linspace(0, 4, 60)[None, :]


def _poly_fc(*boxes):
    """FeatureCollection of lon/lat boxes given as (w, s, e, n)."""
    feats = []
    for w, s, e, n in boxes:
        ring = [[w, s], [e, s], [e, n], [w, n], [w, s]]
        feats.append({"type": "Feature", "properties": {"height_m": 15},
                      "geometry": {"type": "Polygon", "coordinates": [ring]}})
    return {"type": "FeatureCollection", "features": feats}


def _cell_box(tr, r0, r1, c0, c1):
    """Lon/lat box of the grid cells rows r0..r1, cols c0..c1 (end exclusive)."""
    return (tr.c + tr.a * c0, tr.f + tr.e * r1, tr.c + tr.a * c1, tr.f + tr.e * r0)


@pytest.fixture(scope="module")
def world():
    h, w, tr = lonlat_grid(BBOX, 1.0)
    nd = np.zeros((h, w), np.float32)
    nd[40:100, 40:100] = 9.0      # tree block A (60 m square)
    nd[40:100, 200:260] = 9.0     # tree block B
    nd[300:330, 40:70] = 9.0      # tree block C (30 m)
    nd[200:230, 100:130] = 15.0   # a building
    nd[200:230, 130:160] = 9.0    # a crown touching the building's east side
    nd[150:152, 150:152] = 9.0    # tiny speck
    green = _poly_fc(_cell_box(tr, 0, h, 0, w))
    buildings = _poly_fc(_cell_box(tr, 200, 230, 100, 130))
    return dict(h=h, w=w, tr=tr, nd=nd, green=green, buildings=buildings)


def _fetch(world, covers=True):
    got = ("rediam_mdhn", world["nd"], world["tr"]) if covers else None
    return cn.CanopyFetch(ndsm=lambda b, p: got, rgb=lambda b: None)


def _layer(world, **kw):
    return cn.canopy_layer(BBOX, world["buildings"], world["green"], _scale(),
                           fetch=_fetch(world), **kw)


def _terrain():
    sc = _scale()
    return Terrain(np.asarray(sc.z_mm(_dem()), float), BBOX, sc), sc


class TestMask:
    px = (1.0, 1.0)

    def test_needs_height_and_vegetation_and_no_building(self):
        nd = np.zeros((60, 60), np.float32)
        nd[10:40, 10:40] = 8.0
        green = np.zeros_like(nd, bool)
        green[:, :30] = True
        m, _ = cn.canopy_mask(nd, px_m=self.px, mm_per_m=MM_PER_M, green=green)
        assert m[20, 15] and m[20, 29]
        assert not m[20, 35]          # tall but not green
        assert not m[5, 5]            # green but not tall

    def test_low_vegetation_is_not_canopy(self):
        nd = np.full((60, 60), 2.0, np.float32)
        m, _ = cn.canopy_mask(nd, px_m=self.px, mm_per_m=MM_PER_M, green=np.ones((60, 60), bool))
        assert not m.any()

    def test_building_buffer(self):
        nd = np.full((60, 80), 8.0, np.float32)
        bld = np.zeros((60, 80), bool)
        bld[:, :20] = True
        m, _ = cn.canopy_mask(nd, px_m=self.px, mm_per_m=MM_PER_M, buildings=bld,
                              green=np.ones((60, 80), bool))
        assert not m[:, :22].any()    # 2 m buffer
        assert m[:, 30:].any()

    def test_exg_stands_in_for_green(self):
        nd = np.full((40, 40), 8.0, np.float32)
        exg = np.full((40, 40), 0.2, np.float32)
        exg[:, 20:] = 0.05
        m, _ = cn.canopy_mask(nd, px_m=self.px, mm_per_m=MM_PER_M, exg=exg)
        assert m[10, 5] and not m[10, 30]

    def test_no_vegetation_signal_is_empty(self):
        nd = np.full((40, 40), 8.0, np.float32)
        m, _ = cn.canopy_mask(nd, px_m=self.px, mm_per_m=MM_PER_M)
        assert not m.any()

    def test_small_areas_dropped_and_small_holes_filled(self):
        nd = np.zeros((120, 120), np.float32)
        nd[10:12, 10:12] = 8.0         # 4 m2 = 0.16 mm2: dropped
        nd[30:70, 30:70] = 8.0
        m, _ = cn.canopy_mask(nd, px_m=self.px, mm_per_m=MM_PER_M, green=np.ones_like(nd, bool))
        assert not m[10:12, 10:12].any()
        assert m[40, 40]
        small = np.full((120, 120), 8.0, np.float32)
        small[60:63, 60:63] = 0.0      # 9 m2 = 0.36 mm2 < 0.5 mm2: filled
        m2, _ = cn.canopy_mask(small, px_m=self.px, mm_per_m=MM_PER_M,
                               green=np.ones_like(small, bool))
        assert m2[61, 61]
        big = np.full((120, 120), 8.0, np.float32)
        big[50:70, 50:70] = 0.0        # 400 m2 = 16 mm2: stays a hole
        m3, _ = cn.canopy_mask(big, px_m=self.px, mm_per_m=MM_PER_M,
                               green=np.ones_like(big, bool))
        assert not m3[60, 60]

    def test_depinch_joins_corner_contacts(self):
        a = np.zeros((4, 4), bool)
        a[0, 0] = a[1, 1] = True
        out = cn.depinch(a)
        assert out.sum() > a.sum()
        assert not (out[:-1, :-1] & out[1:, 1:] & ~out[:-1, 1:] & ~out[1:, :-1]).any()


class TestHeights:
    def test_clipped_smoothed_and_floored(self):
        nd = np.zeros((60, 60), np.float32)
        nd[10:50, 10:50] = 80.0
        m = nd > 0
        h = cn.canopy_heights(nd, m, mm_per_m=MM_PER_M, px_m=(1.0, 1.0))
        assert np.isnan(h[0, 0])
        assert np.nanmax(h) <= 30.0 + 1e-3
        nd2 = np.where(m, 1.0, 0.0).astype(np.float32)
        assert np.nanmin(cn.canopy_heights(nd2, m, mm_per_m=MM_PER_M, px_m=(1.0, 1.0))) >= 2.5


class TestLayer:
    def test_builds_from_injected_fetch(self, world):
        layer = _layer(world)
        assert layer and layer.provider == "rediam_mdhn"
        assert layer.stats["mask_ha"] > 0
        # buildings and the 2 m buffer excluded; the speck dropped
        assert np.isnan(layer.height_m[198:232, 98:132]).all()
        assert np.isnan(layer.height_m[150:152, 150:152]).all()

    def test_off_without_coverage(self, world):
        layer = cn.canopy_layer(BBOX, world["buildings"], world["green"], _scale(),
                                fetch=_fetch(world, covers=False))
        assert not layer and "no survey" in layer.reason

    def test_off_without_vegetation_signal(self, world):
        layer = cn.canopy_layer(BBOX, world["buildings"], None, _scale(), fetch=_fetch(world))
        assert not layer and "vegetation" in layer.reason

    def test_survey_error_degrades(self, world):
        from city2stl.height.providers._survey import SurveyError

        def boom(b, p):
            raise SurveyError("down")

        layer = cn.canopy_layer(BBOX, None, world["green"], _scale(),
                                fetch=cn.CanopyFetch(ndsm=boom, rgb=lambda b: None))
        assert not layer and "down" in layer.reason

    def test_digest_changes_with_input(self, world):
        a, b = _layer(world), _layer(world, preview=True)
        assert a.digest() == _layer(world).digest()
        assert a.digest() != b.digest()

    def test_cell_size(self):
        tr = Affine(1e-5, 0, -3.6, 0, -1e-5, 37.2)
        r, c = cn.cell_size_m(tr, 0.0)
        assert r == pytest.approx(1e-5 * M_PER_DEG_LAT)
        assert c == pytest.approx(r)


class TestSolids:
    @pytest.fixture(scope="class")
    def built(self, world):
        layer = _layer(world)
        terrain, sc = _terrain()
        solids, stats = cn.canopy_solids(layer, terrain, sc.z_mm_per_m)
        return layer, terrain, sc, solids, stats

    def test_one_solid_per_crown_area_all_watertight(self, built):
        *_, solids, stats = built
        assert stats["solids"] == len(solids) >= 3
        for v, f in solids:
            m = trimesh.Trimesh(v, f, process=True)
            assert m.is_watertight and m.volume > 0

    def test_seated_below_terrain_and_topped_by_height(self, built):
        _layer_, terrain, sc, solids, _ = built
        for v, _f in solids:
            g = terrain.sample(v[:, 0], v[:, 1])
            assert (v[:, 2] <= g + 30 * sc.z_mm_per_m + 1e-6).all()
            assert (v[:, 2] >= g - 0.6 - 1e-6).all()
            assert (v[:, 2] < g - 0.5).any()      # the bottom reaches under the surface
        rise = max((v[:, 2] - terrain.sample(v[:, 0], v[:, 1])).max() for v, _f in solids)
        assert rise == pytest.approx(9.0 * sc.z_mm_per_m, abs=0.25)

    def test_scale_mismatch_raises(self, world):
        layer = _layer(world)
        terrain, sc = _terrain()
        layer.mm_per_m *= 1.5
        with pytest.raises(ValueError, match="scale"):
            cn.canopy_solids(layer, terrain, sc.z_mm_per_m)

    def test_preview_has_fewer_faces_than_full(self, world):
        terrain, sc = _terrain()
        full = cn.canopy_solids(_layer(world), terrain, sc.z_mm_per_m)[1]["faces"]
        prev = cn.canopy_solids(_layer(world, preview=True), terrain, sc.z_mm_per_m)[1]["faces"]
        assert prev < full      # synthetic boxes are simple; the real 10x is measured on Granada


class TestInCityModel:
    @pytest.fixture(scope="class")
    def models(self, world):
        sc = _scale()
        z = sc.z_mm(_dem())
        layers = {"buildings": world["buildings"]}
        base = build_on_terrain(z, BBOX, sc, layers, cache=False)
        with_trees = build_on_terrain(z, BBOX, sc, layers, cache=False, canopy=_layer(world))
        return base, with_trees, sc

    def test_unchanged_with_a_falsy_canopy(self, world, models):
        base, _, sc = models
        off = cn.canopy_layer(BBOX, None, None, sc, fetch=_fetch(world, covers=False))
        cm = build_on_terrain(sc.z_mm(_dem()), BBOX, sc, {"buildings": world["buildings"]},
                              cache=False, canopy=off)
        assert "trees" not in cm.parts and "trees" not in base.parts
        assert np.array_equal(cm.merged.vertices, base.merged.vertices)
        assert "no survey" in cm.report["layers"]["trees"]["off"]

    def test_trees_part_is_watertight_and_never_overlaps_buildings(self, models):
        base, cm, _ = models
        assert "trees" in cm.parts and "buildings" in cm.parts
        assert welded_watertight(cm.parts["trees"]) and welded_watertight(cm.merged)
        # buildings are claimed first: identical with and without trees
        assert cm.parts["buildings"].volume == pytest.approx(base.parts["buildings"].volume,
                                                             rel=1e-6)
        total = sum(p.volume for p in cm.parts.values())
        assert cm.merged.volume == pytest.approx(total, rel=1e-4)    # parts are disjoint

    def test_trees_layer_can_be_switched_off(self, world, models):
        _, _, sc = models
        cm = build_on_terrain(sc.z_mm(_dem()), BBOX, sc, {}, cache=False, canopy=_layer(world),
                              layer_overrides={"trees": {"enabled": False}})
        assert "trees" not in cm.parts

    def test_cache_key_follows_canopy(self, world, tmp_path, monkeypatch):
        monkeypatch.setattr("geo2stl.cache.CACHE_ROOT", tmp_path)
        sc = _scale()
        z = sc.z_mm(_dem())
        a = build_on_terrain(z, BBOX, sc, {}, cache=True, canopy=_layer(world))
        b = build_on_terrain(z, BBOX, sc, {}, cache=True, canopy=_layer(world))
        assert b.report["layers"]["trees"].get("cached") is True
        assert len(a.parts["trees"].faces) == len(b.parts["trees"].faces)
        c = build_on_terrain(z, BBOX, sc, {}, cache=True, canopy=_layer(world, preview=True))
        assert not c.report["layers"]["trees"].get("cached")


def test_grid_resolution_stays_under_the_survey_cap():
    from city2stl.height.providers._survey import MAX_GRID_SIDE, lonlat_grid

    breck = {"north": 39.51, "south": 39.43, "east": -106.03, "west": -106.125}
    res = cn._grid_resolution_m(breck, 1.0)
    assert 2.0 < res < 2.5
    h, w, _ = lonlat_grid(breck, res)          # no SurveyError
    assert max(h, w) <= MAX_GRID_SIDE
    granada = {"north": 37.187, "south": 37.169, "east": -3.582, "west": -3.614}
    assert cn._grid_resolution_m(granada, 1.0) == 1.0   # small boxes keep native resolution
