"""city2stl.registration.street_place: raster helpers, plate model, searches and map layers.

Everything runs on synthetic rasters.  The "city" is a deterministic block pattern defined in
metres, sampled once as the map (`map_buildings` faked) and once as a plate cut from it at a
known offset, so placement has a ground truth to recover.  Overpass, DEM services and the
machine caches are never touched: every network seam raises, CACHE lives in tmp_path.
"""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from city2stl.registration import street_place as sp
from geo2stl.geo import M_PER_DEG_LAT, m_per_deg_lon

LAT0, LON0 = 40.0, -75.0
MLON = m_per_deg_lon(LAT0)
BBOX_META = [LAT0 + 0.005, LAT0 - 0.005, LON0 + 0.005, LON0 - 0.005]


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "CACHE", tmp_path / "street_cache")

    def boom(*a, **k):
        raise AssertionError("network call in an offline test")
    monkeypatch.setattr(sp.osm_model, "elements", boom)
    monkeypatch.setattr(sp.locate, "_overpass", boom)
    monkeypatch.setattr(sp.locate, "osm_water", boom)
    monkeypatch.setattr("geo2stl.osm.overpass_query", boom)
    monkeypatch.setattr("geo2stl.dem.fetch_opentopo_dem", boom)
    monkeypatch.setattr("geo2stl.dem.fetch_h5_dem", boom)
    monkeypatch.setattr("geo2stl.cache.read_array_cache", lambda *a, **k: None)
    monkeypatch.setattr("geo2stl.cache.write_array_cache", boom)


# ------------------------------------------------------------------------------ synthetic city

def _field(x, y):
    """Buildings 22 m square on a 30 m grid, about 60% of the blocks present."""
    bi, bj = np.floor(x / 30).astype(int), np.floor(y / 30).astype(int)
    h = ((bi * 73856093) ^ (bj * 19349663)) % 1000 / 1000.0
    return ((h > 0.4) & ((x % 30) < 22) & ((y % 30) < 22)).astype(np.float32)


def _sample(xc, yc, n, cell, sub=3):
    """Covered fraction of an n x n grid of `cell` m cells centred at (xc, yc) metres; row 0 south."""
    ax = (np.arange(n * sub) + 0.5) / sub * cell - n * cell / 2
    X, Y = np.meshgrid(xc + ax, yc + ax)
    return _field(X, Y).reshape(n, sub, n, sub).mean((1, 3)).astype(np.float32)


def _fake_map_buildings(bbox, grid):
    n, s, e, w = bbox
    xc = ((e + w) / 2 - LON0) * MLON
    yc = ((n + s) / 2 - LAT0) * M_PER_DEG_LAT
    return _sample(xc, yc, grid, (e - w) * MLON / grid)


def _plate(dx_m=0.0, dy_m=0.0, **kw):
    """A 512 m plate (256 cells of 2 m) cut from the synthetic city at (dx, dy) metres of centre."""
    built = _sample(dx_m, dy_m, 256, 2.0)
    relief = built * 20.0
    meta = {"cell_size_m": 2.0, "tallest_m": 20.0, "slug": "syn", "city": "Syn",
            "osm_bbox_nsew": BBOX_META, "resolution": 512}
    return sp.Plate(meta=meta, relief=relief, residual=relief.copy(), gscale=1.0, rot_deg=0.0,
                    **kw)


@pytest.fixture
def fast_search(monkeypatch):
    """Shrink the refine's search (reach, turns, sizes) so a placement takes under a second."""
    monkeypatch.setattr(sp, "REFINE_REACH_M", 100.0)
    monkeypatch.setattr(sp, "QUICK_REACH_M", 100.0)
    monkeypatch.setattr(sp, "DENSITY_SIZES", (1.0,))
    monkeypatch.setattr(sp, "DENSITY_TURNS", np.array([0.0]))
    monkeypatch.setattr(sp, "DENSITY_CANDIDATES", 2)
    monkeypatch.setattr(sp, "QUICK_TURNS", np.array([0.0]))
    monkeypatch.setattr(sp, "QUICK_SIZES", np.array([1.0]))
    orig = sp.refine

    def fast(plate, channels, cell, n, start_turn, start_size, reach_m=100.0,
             turns=np.array([0.0]), sizes=np.array([1.0])):
        return orig(plate, channels, cell, n, start_turn, start_size, reach_m, turns, sizes)
    monkeypatch.setattr(sp, "refine", fast)
    monkeypatch.setattr(sp, "map_buildings", _fake_map_buildings)
    monkeypatch.setattr(sp, "map_parks", lambda b, g: np.zeros((g, g), np.float32))


# ------------------------------------------------------------------------------ raster helpers

def test_small_resamples_to_a_square_by_area():
    a = np.arange(64.0).reshape(8, 8)
    s = sp.small(a, res=4)
    assert s.shape == (4, 4) and s.dtype == np.float32
    assert s[0, 0] == pytest.approx(a[:2, :2].mean())
    assert s.mean() == pytest.approx(a.mean())


def test_smooth_keeps_a_constant_and_spreads_an_impulse():
    flat = np.full((40, 40), 3.0)
    assert sp.smooth(flat, 2.0, 20.0) == pytest.approx(np.full((40, 40), 3.0), abs=1e-5)
    imp = np.zeros((41, 41), np.float32)
    imp[20, 20] = 1.0
    out = sp.smooth(imp, 2.0, 20.0)                  # sigma = 20 / 2 / 2 = 5 cells
    assert out.sum() == pytest.approx(1.0, abs=2e-3)
    assert out[20, 20] < 0.1 and out[20, 20] == out.max()
    # sigma never drops below one cell
    tiny = sp.smooth(imp, 10.0, 1.0)
    assert tiny[20, 20] == pytest.approx(1 / (2 * np.pi), rel=0.05)


def test_standardize_zero_mean_unit_std_inside_and_zero_outside():
    rng = np.random.default_rng(0)
    v = rng.normal(5.0, 3.0, (30, 30)).astype(np.float32)
    valid = np.zeros((30, 30), bool)
    valid[5:25, 5:25] = True
    z = sp.standardize(v, valid)
    assert z[valid].mean() == pytest.approx(0.0, abs=1e-5)
    assert z[valid].std() == pytest.approx(1.0, abs=1e-5)
    assert not z[~valid].any()
    assert not sp.standardize(v, np.zeros_like(valid)).any()
    assert not sp.standardize(np.ones((4, 4), np.float32), np.ones((4, 4), bool)).any()


def test_prepared_is_smooth_then_standardize():
    rng = np.random.default_rng(1)
    v = rng.random((32, 32)).astype(np.float32)
    valid = np.ones((32, 32), bool)
    np.testing.assert_allclose(sp.prepared(v, valid, 2.0, 8.0),
                               sp.standardize(sp.smooth(v, 2.0, 8.0), valid))


def test_merged_closes_narrow_gaps_only():
    a = np.zeros((20, 40), np.float32)
    a[5:15, 5:15] = 1.0
    a[5:15, 19:29] = 1.0                              # a 4-cell gap
    closed = sp.merged(a, 1.0, 6.0)                   # r = 3, kernel 7: the gap closes
    assert closed[10, 16] == 1.0 and closed[10, 2] == 0.0
    open_ = sp.merged(a, 1.0, 2.0)                    # r = 1: it does not
    assert open_[10, 16] == 0.0
    assert sp.merged(a, 1.0, 6.0).dtype == np.float32


def test_metres_per_unit_anchors_on_the_tallest_roof_not_a_spike():
    relief = np.zeros((30, 30), np.float32)
    residual = np.zeros((30, 30), np.float32)
    residual[5:12, 5:12] = 10.0                       # a roof 10 units tall
    residual[20, 20] = 100.0                          # a one-cell spike: eroded away
    assert sp.metres_per_unit(residual, relief, 30.0) == pytest.approx(3.0)
    # cells outside the plate (NaN relief) do not count
    relief[5:12, 5:12] = np.nan
    assert sp.metres_per_unit(residual, relief, 30.0) == pytest.approx(0.3)
    # a plate with no flat top falls back to its tallest cell
    spike = np.zeros((10, 10), np.float32)
    spike[3, 3] = 4.0
    assert sp.metres_per_unit(spike, np.zeros((10, 10), np.float32), 8.0) == pytest.approx(2.0)


def test_built_mask_trims_the_blur_halo_and_the_low_stuff():
    h = np.zeros((30, 60), np.float32)
    h[5:15, 5:15] = 20.0
    h[5:15, 15:17] = 6.0                              # halo: under half of the 20 m top
    h[20:25, 40:45] = 1.5                             # under FLOOR_M
    h[20:25, 10:15] = 4.0                             # a low building on its own: kept
    m = sp.built_mask(h, 2.5)
    assert m[10, 10] and not m[10, 15] and not m[22, 42]
    assert m[22, 12]
    assert m.dtype == bool


def test_built_height_reads_buildings_in_metres_and_adds_back_a_wide_roof():
    cell = 2.5
    relief = np.zeros((160, 160), np.float32)
    residual = np.zeros((160, 160), np.float32)
    relief[20:40, 20:40] = 10.0                       # a 50 m building: in the residual
    residual[20:40, 20:40] = 10.0
    relief[80:140, 80:140] = 15.0                     # a 150 m roof: wider than the 80 m opening
    land = np.isfinite(relief)
    h = sp.built_height(relief, residual, land, cell, mpu=1.0)
    assert h[30, 30] == pytest.approx(10.0, abs=0.5)
    assert h[5, 5] == pytest.approx(0.0, abs=0.01)
    assert h[110, 110] == pytest.approx(15.0, abs=1.5)    # the mesa path
    assert h[110, 110] > sp.FLOOR_M
    # without the mesa path (residual keeps the roof's absence) the roof would be zero
    h0 = sp.built_height(relief, residual, land, cell, mpu=2.0)
    assert h0[30, 30] == pytest.approx(20.0, abs=1.0)
    # a plate with no land at all returns the plain residual-based height
    nothing = sp.built_height(relief, residual, np.zeros_like(land), cell, 1.0)
    assert nothing.shape == relief.shape


def test_mesa_additions_keeps_flat_roofs_and_drops_big_or_sloped_components():
    f = np.zeros((60, 60), np.float32)
    h_big = np.zeros((60, 60), np.float32)
    f[20:40, 20:40] = 15.0
    h_big[20:40, 20:40] = 15.0
    land = np.ones((60, 60), bool)
    base = np.zeros((60, 60), bool)
    flat = np.zeros((60, 60), np.float32)
    out = sp.mesa_additions(f, h_big, base, land, 2.5, flat)
    assert out[30, 30] and not out[5, 5]
    # on sloping ground it is a hill, not a roof
    assert not sp.mesa_additions(f, h_big, base, land, 2.5, flat + 0.5).any()
    # cells already in `base` are other buildings
    assert not sp.mesa_additions(f, h_big, np.ones_like(base), land, 2.5, flat).any()
    # a component bigger than MESA_CAP_M2 is a landform
    assert not sp.mesa_additions(f, h_big, base, land, 30.0, flat).any()
    # a hillside rim: the relief just outside is as high, so it is not a roof
    ramp = np.tile(np.linspace(0, 30, 60, dtype=np.float32), (60, 1))
    assert not sp.mesa_additions(ramp, ramp, base, land, 2.5, flat).any()


# ------------------------------------------------------------------------------ the plate

def test_plate_cell_span_and_built_mask_match_the_cut():
    p = _plate()
    assert p.cell_m == pytest.approx(2.0) and p.span_m() == pytest.approx(512.0)
    assert p.built.shape == (256, 256) and p.built.dtype == np.float32
    assert p.valid.all() and p.water is None
    truth = _sample(0, 0, 256, 2.0) > 0.5
    assert (p.built > 0.5).mean() == pytest.approx(truth.mean(), abs=0.05)
    assert float((p.built.astype(bool) == truth).mean()) > 0.9
    assert p.relief_m == pytest.approx(0.0, abs=1e-4)        # flat ground
    assert p.rot_deg == 0.0 and p.gscale == 1.0 and p.slug == "syn"


def test_plate_defaults_for_unset_guess_and_a_water_cutout():
    built = _sample(0, 0, 128, 4.0)
    relief = built * 20.0
    relief[:, :32] = np.nan                           # outside the plate's outline
    water = np.zeros((128, 128), np.uint8)
    water[:, 90:] = 255
    meta = {"cell_size_m": 4.0, "tallest_m": 20.0, "slug": "w", "osm_bbox_nsew": BBOX_META}
    p = sp.Plate(meta=meta, relief=relief, residual=np.nan_to_num(relief), water=water,
                 gscale=None, rot_deg=None)
    assert p.gscale == pytest.approx(1 / 1.5) and p.rot_deg == 0.0
    assert not p.valid[:, :50].any() and p.valid[:, 80:].all()
    assert p.water is not None and p.water[:, -10:].min() == 1.0 and p.water[:, 100:150].max() == 0.0
    assert p.built[:, -10:].max() == 0.0              # water is not built


def test_plate_non_square_keeps_its_aspect():
    rel = np.zeros((100, 200), np.float32)
    rel[10:30, 10:30] = 8.0
    meta = {"cell_size_m": 2.0, "tallest_m": 8.0, "osm_bbox_nsew": BBOX_META}
    p = sp.Plate(meta=meta, relief=rel, residual=rel.copy(), gscale=1.0)
    assert p.res_hw == (128, 256) and p.built.shape == (128, 256)
    assert p.cell_m == pytest.approx(2.0 * 200 / 256)


def test_plate_loads_a_pack_from_disk(tmp_path, monkeypatch):
    d = tmp_path / "packs" / "disk"
    d.mkdir(parents=True)
    rel = _sample(0, 0, 64, 4.0) * 12.0
    np.save(d / "stl_relief.npy", rel)
    np.save(d / "stl_residual.npy", rel)
    w = np.zeros((64, 64), np.uint8)
    w[:, 50:] = 255
    cv2.imwrite(str(d / "stl_water.png"), w)
    meta = {"cell_size_m": 4.0, "tallest_m": 12.0, "osm_bbox_nsew": BBOX_META, "city": "Disk",
            "pipeline_guess": {"scale": 0.8, "rot_deg": 12.0}}
    (d / "meta.json").write_text(json.dumps(meta))
    monkeypatch.setenv("ALIGN_DATA_DIR", str(tmp_path / "packs"))
    p = sp.Plate("disk")
    assert p.slug == "disk" and p.gscale == 0.8 and p.rot_deg == 12.0
    assert p.water is not None
    assert p.cell_m == pytest.approx(4.0 * 0.8 * 64 / 256)
    # a pack with no guess at all is a surveyed surface spanning the window
    meta.pop("pipeline_guess")
    (d / "meta.json").write_text(json.dumps(meta))
    assert sp.Plate("disk").gscale == 1.0


# ------------------------------------------------------------------------------ posed / geometry

def test_posed_scales_centres_and_rotates_the_layer():
    layer = np.ones((64, 64), np.float32)
    valid = np.ones((64, 64), bool)
    c, v, ext = sp.posed(layer, valid, 2.0, 2.0, 1.0, 0.0, 200)
    assert c.shape == v.shape == (200, 200)
    assert v.sum() == pytest.approx(64 * 64, rel=0.03)
    assert ext == 64
    ys, xs = np.where(v)
    assert (ys.min() + ys.max()) / 2 == pytest.approx(99.5, abs=1.5)      # centred
    assert (xs.min() + xs.max()) / 2 == pytest.approx(99.5, abs=1.5)
    c2, v2, ext2 = sp.posed(layer, valid, 2.0, 2.0, 1.5, 0.0, 200)
    assert ext2 == 96 and v2.sum() == pytest.approx(96 * 96, rel=0.03)
    _, _, ext45 = sp.posed(layer, valid, 2.0, 2.0, 1.0, 45.0, 200)
    assert ext45 == pytest.approx(64 * np.sqrt(2), abs=3)
    # a coarser target cell shrinks the canvas footprint
    _, v3, ext3 = sp.posed(layer, valid, 2.0, 4.0, 1.0, 0.0, 200)
    assert ext3 == 32
    # the layer is multiplied by its mask: nothing outside the outline
    half = valid.copy()
    half[:, 32:] = False
    c4, v4, _ = sp.posed(layer, half, 2.0, 2.0, 1.0, 0.0, 200)
    assert c4[~v4].max() == 0.0


def test_disc_and_window_and_map_cell():
    d = sp.disc((21, 21), 5)
    assert d[10, 10] and d[10, 15] and not d[10, 16] and not d[0, 0]
    assert d.sum() == pytest.approx(np.pi * 25, rel=0.15)

    meta = {"osm_bbox_nsew": BBOX_META}
    bbox, grid, cell = sp.window(meta, 1000.0)
    assert cell == sp.TARGET_CELL_M and grid == 126       # ceil(1000 / 8 / 2) * 2
    n, s, e, w = bbox
    assert (n + s) / 2 == pytest.approx(LAT0) and (e + w) / 2 == pytest.approx(LON0)
    assert (n - s) * M_PER_DEG_LAT == pytest.approx(grid * cell)
    assert (e - w) * MLON == pytest.approx(grid * cell, rel=1e-3)
    b2, g2, c2 = sp.window(meta, 100.0, cell_m=5.0, centre=(41.0, -74.0))
    assert g2 == 20 and c2 == 5.0
    assert (b2[0] + b2[1]) / 2 == pytest.approx(41.0) and (b2[2] + b2[3]) / 2 == pytest.approx(-74.0)

    assert sp.map_cell(_plate()) == 2.5                     # 512 m: a monument-sized plate
    big = sp.Plate(meta={"cell_size_m": 4.0, "tallest_m": 5.0, "osm_bbox_nsew": BBOX_META},
                   relief=np.full((256, 256), 5.0), residual=np.ones((256, 256)),
                   gscale=1.0)
    assert big.span_m() >= sp.SMALL_PLATE_M and sp.map_cell(big) == sp.TARGET_CELL_M


# ------------------------------------------------------------------------------ correlation

def _blocks(n, seed=0, cell=4):
    rng = np.random.default_rng(seed)
    return np.kron((rng.random((n // cell, n // cell)) > 0.5).astype(np.float32),
                   np.ones((cell, cell), np.float32))


def _crop_pose(n, size, y0, x0, seed=0):
    """A map and a plate canvas holding the map's crop at (y0, x0), centred in the canvas."""
    image = _blocks(n, seed)
    canvas = np.zeros((n, n), np.float32)
    pv = np.zeros((n, n), bool)
    c = (n - size) // 2
    canvas[c:c + size, c:c + size] = image[y0:y0 + size, x0:x0 + size]
    pv[c:c + size, c:c + size] = True
    return image, canvas, pv, (y0 - c, x0 - c)


@pytest.mark.parametrize("y0,x0", [(40, 40), (30, 52), (50, 26)])
def test_reference_correlation_peaks_at_the_true_shift(y0, x0):
    image, canvas, pv, (dy, dx) = _crop_pose(128, 40, y0, x0)
    ref = sp.Reference(image, 4.0, (8.0, 16.0))
    surfs = ref.surfaces(canvas, pv)
    assert len(surfs) == 2 and all(s.shape == (128, 128) for s in surfs)
    for s in surfs:
        assert s.max() <= 1.0 and s.min() >= -1.0
        at, peak = sp.argmax_shift(s)
        assert at == (dy, dx) and peak > 0.9
    total = sp.zsurface(ref, canvas, pv, sp.disc((128, 128), 50))
    at, score = sp.argmax_shift(total)
    assert at == (dy, dx) and score > 4.0
    assert sp.runner_up(total, at, 10.0) < score - 1.0


def test_reference_masked_surfaces_with_everywhere_care_equal_the_plain_path():
    image, canvas, pv, _ = _crop_pose(96, 32, 30, 34)
    plain = sp.Reference(image, 4.0, (16.0,))
    all_care = sp.Reference(image, 4.0, (16.0,), care=np.ones((96, 96), bool))
    assert all_care.care is None                       # nothing to mask: the fast path
    np.testing.assert_array_equal(plain.surfaces(canvas, pv)[0], all_care.surfaces(canvas, pv)[0])


def test_reference_care_mask_ignores_uncared_cells():
    image, canvas, pv, (dy, dx) = _crop_pose(128, 40, 40, 44)
    care = np.ones((128, 128), bool)
    care[:30, :] = False                               # a "park" strip far from the answer
    masked = sp.Reference(image * care, 4.0, (16.0,), care=care)
    assert masked.care is not None
    total = sp.zsurface(masked, canvas, pv, sp.disc((128, 128), 50))
    assert sp.argmax_shift(total)[0] == (dy, dx)
    # a shift that leaves under 30% of the plate on cared cells scores exactly 0
    surf = masked.surfaces(canvas, pv)[0]
    assert surf[64 - 44, 64] == 0.0


def test_argmax_shift_runner_up_and_zsurface_floor():
    t = np.full((21, 21), -1.0)
    t[10 + 3, 10 - 4] = 5.0
    t[2, 18] = 3.0
    assert sp.argmax_shift(t) == ((3, -4), 5.0)
    assert sp.runner_up(t, (3, -4), 2.0) == 3.0
    assert sp.runner_up(t, (3, -4), 30.0) == -np.inf        # everything is "near"
    t[10 + 3, 10 - 3] = 4.5                                   # a neighbour inside the radius
    assert sp.runner_up(t, (3, -4), 2.0) == 3.0


# ------------------------------------------------------------------------------ agreement / guess

def test_agreement_scores_the_overlap_of_plate_and_map_footprints():
    p = _plate()
    cell = 2.5
    n = 400
    truth = _fake_map_at(0.0, 0.0, n, cell)
    assert sp.agreement(p, cell, 1.0, 0.0, n, (0, 0), truth) > 0.85
    assert sp.agreement(p, cell, 1.0, 0.0, n, (0, 0), 1.0 - truth) < 0.1
    shifted = sp.agreement(p, cell, 1.0, 0.0, n, (7, -5), truth)
    assert 0.3 < shifted < sp.agreement(p, cell, 1.0, 0.0, n, (0, 0), truth)


def _fake_map_at(xc, yc, n, cell):
    return _sample(xc, yc, n, cell)


def test_as_guess_builds_the_exporters_matrix_from_a_placement():
    meta = {"osm_bbox_nsew": BBOX_META, "resolution": 512,
            "geometric_guess": {"scale": 0.5}}
    centre = {"lat": LAT0, "lon": LON0, "turn_deg": 0.0, "size": 1.2}
    g = sp.as_guess(centre, meta)
    s = 0.5 * 1.2
    M = np.array(g["matrix"])
    np.testing.assert_allclose(M[:, :2], [[s, 0], [0, s]], atol=1e-12)
    np.testing.assert_allclose(M[:, 2], [256 * (1 - s)] * 2, atol=1e-9)    # about the centre
    assert g["scale"] == pytest.approx(s) and g["rot_deg"] == 0.0 and g["source"] == "street_place"
    assert (g["tx"], g["ty"]) == (M[0, 2], M[1, 2])

    # moving the placement east/north by a quarter of the window moves the translation by 128 px
    n, s_, e, w = BBOX_META
    moved = dict(centre, lon=LON0 + (e - w) / 4, lat=LAT0 + (n - s_) / 4)
    gm = np.array(sp.as_guess(moved, meta)["matrix"])
    np.testing.assert_allclose(gm[:, 2] - M[:, 2], [128.0, 128.0], atol=1e-6)

    # rotation convention is cv2's: 90 degrees counter-clockwise on the image
    rot = np.array(sp.as_guess(dict(centre, turn_deg=90.0, size=1.0), meta)["matrix"])
    np.testing.assert_allclose(rot[:, :2], 0.5 * np.array([[0, 1], [-1, 0]]), atol=1e-12)

    # not confident of the size: size 1.0 is kept, the guess scale comes from the pack
    soft = sp.as_guess(dict(centre, confident=False, size=1.3), meta)
    assert soft["scale"] == pytest.approx(0.5)
    # explicit resolution and gscale from the result when the pack has none
    bare = {"osm_bbox_nsew": BBOX_META}
    g2 = sp.as_guess(dict(centre, size=1.0, gscale=0.25), bare, resolution=128)
    assert g2["scale"] == 0.25
    np.testing.assert_allclose(np.array(g2["matrix"])[:, 2], [48.0, 48.0], atol=1e-9)


# ------------------------------------------------------------------------------ searches

def test_place_plate_recovers_a_plate_cut_at_a_known_offset(fast_search):
    dx, dy = 25.0, -15.0                                # metres east / north of the pack's centre
    p = _plate(dx, dy)
    res = sp.place_plate(p, channels=("buildings",))
    cell = res["cell_m"]
    assert cell == 2.5
    east = (res["lon"] - LON0) * MLON
    north = (res["lat"] - LAT0) * M_PER_DEG_LAT
    assert abs(east - dx) <= 1.05 * cell and abs(north - dy) <= 1.05 * cell
    assert res["turn_deg"] == 0.0
    assert res["size"] == 1.0 and res["confident"] is True
    assert res["moved_m"] == pytest.approx(np.hypot(dx, dy), abs=2 * cell)
    assert res["unique"] > 1.0 and res["score"] > res["unique"]
    assert res["agree"] > 0.85 > res["agree_pack"]
    assert res["channels"] == ["buildings"] and res["notes"] == {}
    assert res["span_m"] == pytest.approx(512.0) and res["slug"] == "syn"
    assert res["hidden_start_m"] is None and res["density"] is None
    json.dumps(res)                                     # the record is JSON-safe


def test_place_plate_a_plate_at_its_recorded_position_does_not_move(fast_search):
    res = sp.place_plate(_plate(0.0, 0.0), channels=("buildings",))
    assert res["moved_m"] <= 2 * res["cell_m"]
    assert res["agree_pack"] > 0.9


def test_place_plate_survives_a_failed_parks_and_terrain_layer(fast_search, monkeypatch):
    def down(*a):
        raise RuntimeError("overpass 429")
    monkeypatch.setattr(sp, "map_parks", down)
    monkeypatch.setattr(sp, "map_terrain", down)
    res = sp.place_plate(_plate(10.0, 5.0))             # default channels include terrain
    assert res["notes"]["parks"].startswith("RuntimeError")
    assert res["notes"]["terrain"].startswith("RuntimeError")
    assert res["channels"] == ["buildings"]


def test_place_plate_uses_the_terrain_channel_when_the_dem_has_shape(fast_search, monkeypatch):
    yy = np.mgrid[:492, :492][0].astype(np.float32)
    monkeypatch.setattr(sp, "map_terrain", lambda bbox, n: np.resize(yy[:n, :n] * 0.5, (n, n)))
    res = sp.place_plate(_plate(10.0, 5.0))
    assert res["dem_relief_m"] is not None and res["dem_relief_m"] >= sp.TERRAIN_MIN_RELIEF_M
    assert res["channels"] == ["buildings", "terrain"]
    assert set(res["channel_z"]) == {"buildings", "terrain"}


def test_place_plate_hidden_start_finds_the_plate_with_the_density_search(fast_search, monkeypatch):
    monkeypatch.setattr(sp, "map_terrain_wide", lambda b, g: np.zeros((g, g), np.float32))
    dx, dy = 20.0, -10.0
    res = sp.place_plate(_plate(dx, dy), hide_pose_m=60.0, channels=("buildings",))
    east = (res["lon"] - LON0) * MLON
    north = (res["lat"] - LAT0) * M_PER_DEG_LAT
    assert abs(east - dx) <= 3 * res["cell_m"] and abs(north - dy) <= 3 * res["cell_m"]
    assert res["hidden_start_m"] == 60.0 and res["agree_pack"] is None
    assert res["density"]["candidates"] and res["density"]["score"] > 0


def test_density_search_ranks_the_true_district_first(fast_search):
    cell, n = 5.0, 300
    plate = _plate(0.0, 0.0)
    # The map window is centred 75 m east and 50 m south of the plate's district, so the plate
    # sits 75 m west and 50 m north of the window centre.
    buildings = _sample(75.0, -50.0, n, cell)
    cands = sp.density_search(plate, buildings, cell, n, 0.0, reach_m=400.0)
    assert 1 < len(cands) <= sp.DENSITY_CANDIDATES
    best = cands[0]
    assert all(cands[i]["score"] >= cands[i + 1]["score"] for i in range(len(cands) - 1))
    assert (best["size"], best["turn"]) == (1.0, 0.0)
    assert abs(best["at"][0] - 50.0 / cell) <= 3 and abs(best["at"][1] + 75.0 / cell) <= 3
    assert best["unique"] == pytest.approx(best["score"] - cands[1]["score"])
    assert best["unique"] > 0.5
    # the candidates are distinct places, at least half a plate apart
    far = np.hypot(cands[0]["at"][0] - cands[1]["at"][0], cands[0]["at"][1] - cands[1]["at"][1])
    assert far * cell >= 0.5 * plate.span_m() - 2 * cell


def test_refine_picks_the_right_size_and_reports_margins():
    cell, n = 2.5, 320
    plate = _plate(0.0, 0.0)
    img = _sample(0.0, 0.0, n, cell)
    chan = [sp.Channel("buildings", plate.built, img, cell, (sp.REFINE_BLOCK_M,), 1.0)]
    out = sp.refine(plate, chan, cell, n, 0.0, 1.0, reach_m=60.0,
                    turns=np.array([-6.0, 0.0, 6.0]), sizes=np.array([0.9, 1.0, 1.05]))
    assert out["size"] == 1.0 and out["turn"] == 0.0
    assert abs(out["at"][0]) <= 1 and abs(out["at"][1]) <= 1
    assert out["size_margin"] > 1.0 and out["unique"] > 1.0
    assert out["at_start"] == pytest.approx(out["score"], abs=0.5)
    assert set(out["channels"]) == {"buildings"} and "total" not in out


def test_score_pose_sums_weighted_channel_surfaces():
    cell, n = 2.5, 320
    plate = _plate(0.0, 0.0)
    img = _sample(0.0, 0.0, n, cell)
    a = sp.Channel("a", plate.built, img, cell, (16.0,), 1.0)
    b = sp.Channel("b", plate.built, img, cell, (16.0,), 0.5)
    inside = sp.disc((n, n), 30)
    total, per = sp.score_pose([a, b], plate, cell, 1.0, 0.0, n, inside)
    np.testing.assert_allclose(total[inside], 1.5 * per["a"][inside], rtol=1e-9)
    assert set(per) == {"a", "b"}


# ------------------------------------------------------------------------------ place / packs / main

def _write_pack(root, slug, dx=12.5, dy=-7.5):
    d = root / slug
    d.mkdir(parents=True)
    p = _plate(dx, dy)
    rel = _sample(dx, dy, 256, 2.0) * 20.0
    np.save(d / "stl_relief.npy", rel)
    np.save(d / "stl_residual.npy", rel)
    meta = dict(p.meta, slug=slug, geometric_guess={"scale": 1.0, "rot_deg": 0.0})
    (d / "meta.json").write_text(json.dumps(meta))
    return d


def test_place_writes_placement_json_to_the_data_dir(tmp_path, monkeypatch, fast_search):
    monkeypatch.setenv("ALIGN_DATA_DIR", str(tmp_path / "data"))
    _write_pack(tmp_path / "data", "alpha")
    res = sp.place("alpha", channels=("buildings",))
    out = json.loads((tmp_path / "data" / "alpha" / "placement.json").read_text())
    assert out["slug"] == "alpha" and out["lat"] == pytest.approx(res["lat"])
    assert abs((out["lon"] - LON0) * MLON - 12.5) <= 2.7
    # write=False leaves the file alone
    (tmp_path / "data" / "alpha" / "placement.json").unlink()
    sp.place("alpha", channels=("buildings",), write=False)
    assert not (tmp_path / "data" / "alpha" / "placement.json").exists()


def test_packs_lists_directories_with_both_plate_arrays(tmp_path, monkeypatch):
    root = tmp_path / "data"
    _write_pack(root, "b_pack")
    _write_pack(root, "a_pack")
    half = root / "half"
    half.mkdir()
    np.save(half / "stl_relief.npy", np.zeros((4, 4)))
    (root / "a_file.txt").write_text("x")
    monkeypatch.setenv("ALIGN_DATA_DIR", str(root))
    assert sp.packs() == ["a_pack", "b_pack"]


def test_safe_turns_an_exception_into_a_result_row(monkeypatch):
    monkeypatch.setattr(sp, "place", lambda *a: (_ for _ in ()).throw(ValueError("bad pack")))
    r = sp._safe(("x", 0.0, ("buildings",)))
    assert r["slug"] == "x" and r["error"] == "ValueError: bad pack" and "Traceback" in r["trace"]
    monkeypatch.setattr(sp, "place", lambda slug, hide, chans: {"slug": slug, "hide": hide})
    assert sp._safe(("y", 5.0, ())) == {"slug": "y", "hide": 5.0}


class _InlinePool:
    def __init__(self, procs):
        self.procs = procs

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def imap_unordered(self, fn, items):
        return map(fn, items)


def test_main_prints_a_table_and_writes_the_json(tmp_path, monkeypatch, capsys):
    ok = {"slug": "good", "city": "Goodville", "moved_m": 12.0, "turn_deg": 3.0,
          "recorded_turn_deg": 2.0, "size": 1.0, "score": 9.5, "unique": 4.2, "size_margin": 2.0,
          "agree": 0.91, "agree_pack": None, "channel_z": {"buildings": 9.5},
          "dem_relief_m": None, "notes": {}}
    rows = {"good": ok, "bad": {"slug": "bad", "error": "KeyError: 'x'", "trace": "TB"}}
    monkeypatch.setattr(sp.mp, "Pool", _InlinePool)
    monkeypatch.setattr(sp, "place", lambda slug, hide, chans: rows[slug])
    out = tmp_path / "out.json"
    sp.main(["good", "bad", "--procs", "1", "--out", str(out), "--channels", "buildings"])
    text = capsys.readouterr().out
    assert "Goodville" in text and "buildings=9.5" in text
    assert "bad" in text and "failed: KeyError: 'x'" in text
    assert [r["slug"] for r in json.loads(out.read_text())] == ["good", "bad"]


def test_main_without_slugs_places_every_pack(tmp_path, monkeypatch, capsys):
    _write_pack(tmp_path / "data", "one")
    monkeypatch.setenv("ALIGN_DATA_DIR", str(tmp_path / "data"))
    seen = []
    monkeypatch.setattr(sp.mp, "Pool", _InlinePool)
    monkeypatch.setattr(sp, "place", lambda slug, hide, chans: seen.append((slug, hide, chans))
                        or {"slug": slug, "error": "stub", "trace": ""})
    sp.main(["--hide", "150"])
    assert seen == [("one", 150.0, ("buildings", "water", "terrain"))]
    assert "failed: stub" in capsys.readouterr().out


# ------------------------------------------------------------------------------ map layers

def _els_by_selector(table):
    calls = []

    def fake(selector, bbox, cache, kind, nodes=False):
        calls.append(kind)
        return table.get(kind, [])
    return fake, calls


def _ring(x0, y0, x1, y1, bbox):
    n, s, e, w = bbox
    lon = lambda f: w + f * (e - w)       # noqa: E731
    lat = lambda f: s + f * (n - s)       # noqa: E731
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return [{"lat": lat(fy), "lon": lon(fx)} for fx, fy in pts]


BBOX = (40.01, 40.0, -75.0, -75.01)


def test_map_buildings_burns_standing_footprints_and_raised_decks_then_caches(monkeypatch):
    house = {"type": "way", "id": 1, "tags": {"building": "yes"},
             "geometry": _ring(0.1, 0.1, 0.4, 0.4, BBOX)}
    ruin = {"type": "way", "id": 2, "tags": {"building": "ruins"},
            "geometry": _ring(0.6, 0.6, 0.9, 0.9, BBOX)}
    bridge = {"type": "way", "id": 3, "tags": {"highway": "primary", "bridge": "yes"},
              "geometry": [{"lat": 40.005, "lon": -75.01}, {"lat": 40.005, "lon": -75.0}]}
    ground_road = {"type": "way", "id": 4, "tags": {"highway": "primary"},
                   "geometry": [{"lat": 40.002, "lon": -75.01}, {"lat": 40.002, "lon": -75.0}]}
    fake, calls = _els_by_selector({"buildings": [house, ruin],
                                    "road_bridges": [bridge, ground_road],
                                    "road_layers": [bridge]})       # duplicates are merged
    monkeypatch.setattr(sp.osm_model, "elements", fake)
    m = sp.map_buildings(BBOX, 40)
    assert m.shape == (40, 40) and m.dtype == np.float32
    assert m[10, 10] == 1.0                              # house, south-west (row 0 south)
    assert m[32, 32] == 0.0                              # ruin is not standing
    assert 0.15 < m[20, 5] < 0.7 and 0.15 < m[20, 35] < 0.7            # the bridge deck at lat 40.005
    assert m[8, 20] == 0.0                               # the ground-level road is not raised
    assert calls == ["buildings", "road_bridges", "road_layers", "rail"]
    again = sp.map_buildings(BBOX, 40)                   # second call: from the .npy cache
    np.testing.assert_array_equal(m, again)
    assert len(calls) == 4
    assert any(p.name.startswith("standing_") for p in sp.CACHE.iterdir())


def test_map_parks_covers_park_polygons(monkeypatch):
    park = {"type": "way", "id": 9, "tags": {"leisure": "park"},
            "geometry": _ring(0.5, 0.0, 1.0, 0.5, BBOX)}
    fake, calls = _els_by_selector({"parks": [park]})
    monkeypatch.setattr(sp.osm_model, "elements", fake)
    m = sp.map_parks(BBOX, 20)
    assert m[3, 15] == 1.0 and m[15, 3] == 0.0 and m[3, 3] == 0.0
    sp.map_parks(BBOX, 20)
    assert calls == ["parks"]


def test_map_water_bridges_cuts_a_gap_at_every_deck(monkeypatch):
    grid = 40
    filled = np.zeros((grid, grid), np.float32)
    filled[:, 15:25] = 1.0                               # a river running north
    monkeypatch.setattr(sp.locate, "osm_water",
                        lambda bbox, g, verbose=False: {"filled": filled, "missing": []})
    deck = {"type": "way", "geometry": [{"lat": 40.005, "lon": -75.01}, {"lat": 40.005, "lon": -75.0}]}

    def over(selector, bbox, attempts=3):
        if selector == sp.locate.SELECTORS["water"]:
            return []
        return [deck] if selector == sp.BRIDGE_SELECTORS[0] else []
    monkeypatch.setattr(sp.locate, "_overpass", over)
    out = sp.map_water_bridges(BBOX, grid)
    assert out.dtype == np.float32 and out[5, 20] == 1.0 and out[35, 20] == 1.0
    assert out[20, 15:25].max() == 0.0                   # the deck is dry across the river
    assert out[5, 5] == 0.0
    # served from the cache the second time: no further Overpass
    monkeypatch.setattr(sp.locate, "osm_water", lambda *a, **k: pytest.fail("not cached"))
    np.testing.assert_array_equal(sp.map_water_bridges(BBOX, grid), out)


def test_map_water_bridges_refuses_when_a_water_layer_failed(monkeypatch):
    monkeypatch.setattr(sp.locate, "osm_water",
                        lambda bbox, g, verbose=False: {"filled": np.zeros((g, g)),
                                                        "missing": ["coast"]})
    with pytest.raises(RuntimeError, match="water layers failed: \\['coast'\\]"):
        sp.map_water_bridges(BBOX, 16)
    assert not any(p.name.startswith("waterbridges") for p in sp.CACHE.iterdir())


def test_islands_are_the_inner_rings_of_water_relations(monkeypatch):
    outer = _ring(0.0, 0.0, 1.0, 1.0, BBOX)
    inner = _ring(0.4, 0.4, 0.6, 0.6, BBOX)
    rel = {"type": "relation", "members": [
        {"role": "outer", "geometry": outer}, {"role": "inner", "geometry": inner}]}
    way = {"type": "way", "geometry": outer}
    monkeypatch.setattr(sp.locate, "_overpass", lambda sel, bbox, attempts=3: [rel, way])
    isl = sp._islands(BBOX, 20)
    assert isl[10, 10] == 1.0 and isl[2, 2] == 0.0
    assert isl.sum() == pytest.approx(0.04 * 400, rel=0.5)
    monkeypatch.setattr(sp.locate, "_overpass", lambda sel, bbox, attempts=3: [way])
    assert not sp._islands(BBOX, 20).any()


def test_map_terrain_samples_the_city_tile_row_zero_south(monkeypatch):
    tile = np.tile(np.linspace(100.0, 0.0, 50, dtype=np.float32)[:, None], (1, 50))  # north high
    tbox = (40.1, 39.9, -74.9, -75.1)
    monkeypatch.setattr(sp, "_terrain_tile", lambda lat, lon: (tile, tbox, "COP30"))
    out = sp.map_terrain((40.01, 40.0, -75.0, -75.01), 8)
    assert out.shape == (8, 8)
    assert np.all(np.diff(out[:, 4]) > 0)                # row 0 south: elevation rises with row
    assert out[0, 0] == pytest.approx(100 * (1 - (0.0625 + 0.0 + 0.5 / 8 * 0.05 / 0.2 * 0) ) , abs=60)


def test_terrain_tile_fetches_caches_and_falls_back_to_the_local_store(monkeypatch):
    import geo2stl.dem as geodem
    saved = {}
    monkeypatch.setattr("geo2stl.cache.write_array_cache",
                        lambda ns, key, arrs, meta: saved.update(ns=ns, meta=meta, arrs=arrs))
    dem = np.arange(16.0, dtype=np.float32).reshape(4, 4)
    dem[0, 0] = np.nan
    calls = []

    def cop(n, s, e, w, product, key, size):
        calls.append(product)
        return dem
    monkeypatch.setattr(geodem, "fetch_opentopo_dem", cop)
    arr, bbox, source = sp._terrain_tile(40.012, -75.013)
    assert source == "COP30" and calls == ["COP30"]
    assert np.isfinite(arr).all() and arr.dtype == np.float32
    assert saved["ns"] == "align_terrain" and saved["meta"]["source"] == "COP30"
    n, s, e, w = bbox                                    # snapped to the 0.05 degree grid
    assert (n + s) / 2 == pytest.approx(40.0) and (e + w) / 2 == pytest.approx(-75.0)
    assert (n - s) * M_PER_DEG_LAT == pytest.approx(sp.TERRAIN_TILE_SIDE_M)

    def no_cop(*a):
        raise OSError("no key")
    monkeypatch.setattr(geodem, "fetch_opentopo_dem", no_cop)
    monkeypatch.setattr(geodem, "fetch_h5_dem",
                        lambda n, s, e, w, h5_file=None: np.ones((3, 3), np.float32) * 7)
    _, _, source = sp._terrain_tile(40.0, -75.0)
    assert source == "SRTM-h5" and saved["meta"]["source"] == "SRTM-h5"

    monkeypatch.setattr(geodem, "fetch_h5_dem",
                        lambda n, s, e, w, h5_file=None: np.full((3, 3), np.nan, np.float32))
    with pytest.raises(RuntimeError, match="DEM tile is empty"):
        sp._terrain_tile(40.0, -75.0)


def test_terrain_tile_reads_a_cache_hit_without_fetching(monkeypatch):
    dem = np.full((5, 5), 3.0, np.float32)
    monkeypatch.setattr("geo2stl.cache.read_array_cache",
                        lambda ns, key: ({"dem": dem}, {"bbox": [1, 0, 1, 0], "source": "COP30"}))
    arr, bbox, source = sp._terrain_tile(40.0, -75.0)
    assert arr is dem and bbox == (1, 0, 1, 0) and source == "COP30"


def test_map_terrain_wide_flips_the_h5_dem_to_row_zero_south_and_caches(monkeypatch):
    import geo2stl.dem as geodem
    dem = np.tile(np.linspace(50.0, 0.0, 12, dtype=np.float32)[:, None], (1, 12))   # row 0 north
    dem[3, 3] = np.nan
    calls = []

    def h5(n, s, e, w, h5_file=None):
        calls.append(h5_file.name)
        return dem
    monkeypatch.setattr(geodem, "fetch_h5_dem", h5)
    out = sp.map_terrain_wide(BBOX, 6)
    assert out.shape == (6, 6) and np.isfinite(out).all()
    assert out[0].mean() < out[-1].mean()                # south low, north high
    assert calls == ["strm_data.h5"]
    monkeypatch.setattr(geodem, "fetch_h5_dem", lambda *a, **k: pytest.fail("not cached"))
    np.testing.assert_array_equal(sp.map_terrain_wide(BBOX, 6), out)
