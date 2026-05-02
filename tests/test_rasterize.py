"""Tests for city2stl/rasterize.py — roof shape painters."""

from __future__ import annotations

import numpy as np
import pytest

from city2stl.rasterize import (
    _direction_tag_to_deg,
    _flat_surface,
    _gabled_surface,
    _hipped_surface,
    _paint_building_roof,
    _principal_axis_deg,
    _pyramidal_surface,
    _skillion_surface,
    rasterize_city_data,
)


def _square_mask(size=20, pad=10) -> np.ndarray:
    """Return a (size+2*pad, size+2*pad) bool mask with a centred size×size square."""
    m = np.zeros((size + 2 * pad, size + 2 * pad), dtype=bool)
    m[pad:pad + size, pad:pad + size] = True
    return m


def _rect_mask(h=10, w=30, pad=10) -> np.ndarray:
    m = np.zeros((h + 2 * pad, w + 2 * pad), dtype=bool)
    m[pad:pad + h, pad:pad + w] = True
    return m


# ── Flat surface ────────────────────────────────────────────────────────────
def test_flat_surface_constant_height():
    mask = _square_mask()
    out = _flat_surface(mask, eaves_h=8.0, roof_h=2.0)
    inside = out[mask]
    outside = out[~mask]
    assert np.all(inside == 10.0)
    assert np.all(np.isnan(outside))


# ── Gabled ──────────────────────────────────────────────────────────────────
def test_gabled_ridge_is_max():
    """Gabled roof: ridge has eaves+roof_h, edges have eaves."""
    mask = _rect_mask(h=10, w=30)  # wide rectangle, ridge along long axis
    out = _gabled_surface(mask, eaves_h=8.0, roof_h=4.0, axis_deg=0.0)  # ridge along x
    # Centre row (along ridge) should be near peak
    cy = out.shape[0] // 2
    centre_strip = out[cy, ~np.isnan(out[cy])]
    assert centre_strip.max() == pytest.approx(12.0, abs=0.5)
    # Top/bottom edge of footprint should be near eaves
    inside = out[mask]
    assert inside.min() == pytest.approx(8.0, abs=0.5)
    assert inside.max() == pytest.approx(12.0, abs=0.5)


def test_gabled_axis_rotation_changes_ridge_direction():
    """Rotating the ridge axis rotates which edges are eaves vs ridge."""
    mask = _square_mask(size=30, pad=5)
    h_horiz = _gabled_surface(mask, eaves_h=8.0, roof_h=4.0, axis_deg=0.0)
    h_vert = _gabled_surface(mask, eaves_h=8.0, roof_h=4.0, axis_deg=90.0)
    # The two are not equal — different ridge directions yield different surfaces
    diff = np.nanmax(np.abs(h_horiz - h_vert))
    assert diff > 0.1


# ── Pyramidal ───────────────────────────────────────────────────────────────
def test_pyramidal_peak_at_centre():
    mask = _square_mask(size=20)
    out = _pyramidal_surface(mask, eaves_h=8.0, roof_h=4.0)
    inside = out[mask]
    cy, cx = (np.array(mask.shape) // 2).tolist()
    centre_val = out[cy, cx]
    assert centre_val == pytest.approx(12.0, abs=0.5)
    assert inside.min() == pytest.approx(8.0, abs=1.0)


# ── Skillion ────────────────────────────────────────────────────────────────
def test_skillion_ramps_along_axis():
    mask = _rect_mask(h=10, w=30)
    out = _skillion_surface(mask, eaves_h=8.0, roof_h=4.0, axis_deg=0.0)
    inside_only = out.copy()
    inside_only[~mask] = np.nan
    # Leftmost column inside footprint should be near eaves; rightmost near peak
    inside_cols = np.where(np.any(mask, axis=0))[0]
    left_col = inside_cols[0]
    right_col = inside_cols[-1]
    left_vals = inside_only[:, left_col]
    right_vals = inside_only[:, right_col]
    assert np.nanmean(left_vals) == pytest.approx(8.0, abs=0.5)
    assert np.nanmean(right_vals) == pytest.approx(12.0, abs=0.5)


# ── Hipped ──────────────────────────────────────────────────────────────────
def test_hipped_max_at_centre():
    mask = _square_mask(size=20)
    out = _hipped_surface(mask, eaves_h=8.0, roof_h=4.0)
    cy, cx = (np.array(mask.shape) // 2).tolist()
    assert out[cy, cx] == pytest.approx(12.0, abs=0.5)
    inside = out[mask]
    # Eaves at boundary
    assert inside.min() == pytest.approx(8.0, abs=1.0)
    assert inside.max() == pytest.approx(12.0, abs=0.5)


# ── Small footprint fallback ────────────────────────────────────────────────
def test_small_footprint_falls_back_to_flat():
    """Buildings smaller than _MIN_ROOF_PIXELS pixels paint flat regardless of shape."""
    # Tiny 2×2 mask
    mask = np.zeros((10, 10), dtype=bool)
    mask[4:6, 4:6] = True
    out = _paint_building_roof(mask, 8.0, 4.0, "gabled")
    inside = out[mask]
    # All values should be the same (flat) at eaves+roof = 12
    assert np.all(inside == 12.0)


# ── Dispatch ────────────────────────────────────────────────────────────────
def test_paint_dispatch_unknown_shape_is_flat():
    mask = _square_mask()
    out = _paint_building_roof(mask, 8.0, 4.0, "fancy_unrecognised_shape")
    inside = out[mask]
    assert np.all(inside == 12.0)


def test_paint_dispatch_flat_explicit():
    mask = _square_mask()
    out = _paint_building_roof(mask, 8.0, 4.0, "flat")
    inside = out[mask]
    assert np.all(inside == 12.0)


def test_paint_zero_roof_height_is_flat_at_eaves():
    """roof_h=0 → flat top at eaves height."""
    mask = _square_mask()
    out = _paint_building_roof(mask, 8.0, 0.0, "gabled")
    inside = out[mask]
    assert np.all(inside == 8.0)


# ── PCA principal axis ──────────────────────────────────────────────────────
def test_principal_axis_horizontal_rect():
    """Long horizontal rectangle → principal axis ≈ 0° or 180° (along x)."""
    mask = _rect_mask(h=8, w=30)
    angle = _principal_axis_deg(mask)
    # Allow either +0 or +180 (PCA is sign-invariant)
    a = abs(angle) % 180
    assert a < 5.0 or a > 175.0


def test_principal_axis_vertical_rect():
    """Long vertical rectangle → principal axis ≈ 90° (or -90°)."""
    mask = _rect_mask(h=30, w=8)
    angle = _principal_axis_deg(mask)
    a = abs(angle) % 180
    assert abs(a - 90.0) < 5.0


# ── Direction tag parsing ───────────────────────────────────────────────────
@pytest.mark.parametrize("tag,expected", [
    ("0", 90.0),    # bearing 0 = North = math angle 90°
    ("90", 0.0),    # bearing 90 = East = math angle 0°
    ("180", -90.0), # bearing 180 = South = math angle -90°
    ("N", 90.0),
    ("E", 0.0),
    ("S", -90.0),
    ("W", 180.0),
])
def test_direction_tag_parsing(tag, expected):
    got = _direction_tag_to_deg(tag)
    assert got is not None
    # Normalize to (-180, 180]
    diff = (got - expected + 180) % 360 - 180
    assert abs(diff) < 0.1


def test_direction_tag_invalid():
    assert _direction_tag_to_deg(None) is None
    assert _direction_tag_to_deg("") is None
    assert _direction_tag_to_deg("not-a-direction") is None


# ── End-to-end rasterize_city_data ─────────────────────────────────────────
def _building_geojson(north, south, east, west, height_m, roof_shape="flat",
                      roof_height_m=None):
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "height_m": height_m,
                "roof:shape": roof_shape,
                "roof_height_m": roof_height_m,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [west, south], [east, south],
                    [east, north], [west, north],
                    [west, south],
                ]],
            },
        }],
    }


def test_rasterize_flat_path_unchanged_when_disabled():
    """With roof_shapes=False the existing flat-top behaviour is preserved."""
    n, s = 40.001, 40.000
    e, w = -75.000, -75.001
    bld = _building_geojson(n, s, e, w, height_m=20.0, roof_shape="gabled")
    empty = {"type": "FeatureCollection", "features": []}
    res = rasterize_city_data(n, s, e, w, dim=64,
                              buildings_geojson=bld, roads_geojson=empty,
                              waterways_geojson=empty, roof_shapes=False)
    grid = np.array(res["values"], dtype=np.float32).reshape(64, 64)
    inside = grid[grid > 0]
    # Flat path: all positive pixels should be exactly height_m * 1.0
    assert inside.max() == pytest.approx(20.0, abs=0.01)
    # And the surface is constant inside
    assert np.allclose(inside, inside[0], atol=0.01)


def test_rasterize_roof_path_produces_height_variation():
    """With roof_shapes=True a gabled building shows a height range."""
    n, s = 40.001, 40.000
    e, w = -75.000, -75.001
    bld = _building_geojson(n, s, e, w, height_m=20.0, roof_shape="gabled",
                            roof_height_m=6.0)
    empty = {"type": "FeatureCollection", "features": []}
    res = rasterize_city_data(n, s, e, w, dim=64,
                              buildings_geojson=bld, roads_geojson=empty,
                              waterways_geojson=empty, roof_shapes=True)
    grid = np.array(res["values"], dtype=np.float32).reshape(64, 64)
    inside = grid[grid > 0]
    assert inside.size >= 9  # need at least _MIN_ROOF_PIXELS to escape flat fallback
    # Eaves at 14, ridge at 20 — there must be non-trivial spread
    spread = float(inside.max() - inside.min())
    assert spread > 2.0
    assert inside.max() <= 20.5
    # Eaves should be near 14 (height_m - roof_h = 20 - 6)
    assert inside.min() <= 16.0
