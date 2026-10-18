"""Projection wings: largest_valid_rect and the export-side crop (ExportContext)."""
import numpy as np

from app.server.core.export import _prepare_dem_array
from app.server.core.export_params import ExportContext
from geo2stl.projections import largest_valid_rect, wing_mask


def _laea_like(h=60, w=80, curve=18):
    """Rectangle with NaN wings in the four corners, curved and joined to the border."""
    yy, xx = np.mgrid[0:h, 0:w]
    dx = np.minimum(xx, w - 1 - xx)
    dy = np.minimum(yy, h - 1 - yy)
    g = 100.0 + xx + yy
    g[(dx < curve) & (dy < curve) & (dx + dy < curve)] = np.nan
    return g


def test_rect_has_no_wing_cells():
    g = _laea_like()
    r0, r1, c0, c1 = largest_valid_rect(g)
    assert np.isfinite(g[r0:r1, c0:c1]).all()
    assert (r1 - r0) * (c1 - c0) > 0.5 * g.size
    assert (r0, r1, c0, c1) != (0, 60, 0, 80)


def test_rect_is_maximal_against_brute_force():
    g = _laea_like(20, 24, 7)
    r0, r1, c0, c1 = largest_valid_rect(g)
    best = 0
    for a in range(20):
        for b in range(a + 1, 21):
            for c in range(24):
                for d in range(c + 1, 25):
                    if np.isfinite(g[a:b, c:d]).all():
                        best = max(best, (b - a) * (d - c))
    assert (r1 - r0) * (c1 - c0) == best


def test_interior_void_is_not_a_wing():
    g = np.ones((30, 30))
    g[10:15, 10:15] = np.nan
    assert not wing_mask(g).any()
    assert largest_valid_rect(g) == (0, 30, 0, 30)


def test_interior_void_does_not_shrink_wing_crop():
    g = _laea_like()
    g[28:32, 38:42] = np.nan
    r0, r1, c0, c1 = largest_valid_rect(g)
    assert np.isnan(g[r0:r1, c0:c1]).sum() == 16


def test_no_wings_full_grid():
    assert largest_valid_rect(np.ones((7, 9))) == (0, 7, 0, 9)


def test_coarse_search_still_wing_free():
    g = _laea_like(700, 900, 200)
    r0, r1, c0, c1 = largest_valid_rect(g, max_cells=64)
    assert np.isfinite(g[r0:r1, c0:c1]).all()
    assert (r1 - r0) * (c1 - c0) > 0.5 * g.size


BBOX = {"north": 50.0, "south": 40.0, "east": 20.0, "west": 0.0}


def _body(g, **dem):
    return {"dem_values": [None if np.isnan(v) else float(v) for v in g.ravel()],
            "height": g.shape[0], "width": g.shape[1], "bbox": dict(BBOX),
            "dem": dem, "median_size": 1}


def test_export_context_crops_wings_and_bbox():
    g = _laea_like()
    p = ExportContext.from_request(_body(g, crop_wings=True))
    assert (p.height, p.width) != g.shape
    assert len(p.dem_values) == p.height * p.width
    assert np.isfinite(p.dem_values).all()
    # bbox shrank by the kept fraction of columns / rows
    assert abs((p.bbox["east"] - p.bbox["west"]) / 20.0 - p.width / 80) < 1e-9
    assert abs((p.bbox["north"] - p.bbox["south"]) / 10.0 - p.height / 60) < 1e-9
    im, _, _, _ = _prepare_dem_array(p)
    assert im.shape == (p.height, p.width)
    # no NaN-derived floor slab: every cell is real terrain (all values differ along the ramp)
    assert np.unique(im).size > 10


def test_export_context_crop_off_by_default_keeps_grid():
    g = _laea_like()
    p = ExportContext.from_request(_body(g))
    assert (p.height, p.width) == g.shape and p.bbox == BBOX


def test_export_context_no_wings_unchanged():
    g = np.arange(24.0).reshape(4, 6)
    p = ExportContext.from_request(_body(g, crop_wings=True))
    assert (p.height, p.width) == (4, 6) and p.dem_values == g.ravel().tolist()
