"""``dim`` is the longest side AFTER projection (user decision 2026-10-13).

Covers geo2stl.projections::projected_shape / source_dim / source_shape and
``project_coordinates(target_dim=...)``, and that the DEM route and every layer
route aligned with it return the same grid (TEST_MODE, no network).
"""

import base64
import io

import numpy as np
import pytest

from geo2stl.projections import (
    project_grid,
    projected_shape,
    source_dim,
    source_shape,
)

# The SanFranciso region from the bug report: 474 x 430 before, ~600 x 545 after.
SF = (37.8217, 37.6621, -122.3314, -122.5552)
TALL = (46.0, 44.0, 7.5, 7.0)          # taller than wide in every projection
PROJECTIONS = ["cosine", "mercator", "equidistant", "lambert",
               "sinusoidal", "miller", "gall"]


@pytest.mark.parametrize("projection", ["none", *PROJECTIONS])
@pytest.mark.parametrize("bbox", [SF, TALL])
def test_projected_shape_longest_side_is_dim(projection, bbox):
    assert max(projected_shape(bbox, projection, 600)) == 600


def test_sf_cosine_is_600_wide():
    assert projected_shape(SF, "cosine", 600) == (541, 600)
    # The raw grid is sampled wider so the projection only shrinks it.
    assert source_dim(600, SF, "cosine") == 759
    assert source_shape(600, SF, "cosine") == (541, 759)
    # Shape-preserving modes sample at dim itself.
    assert source_dim(600, SF, "none") == 600
    assert source_dim(600, SF, "cosine", maintain_dimensions=True) == 600


@pytest.mark.parametrize("projection", ["cosine", "mercator", "sinusoidal", "lambert"])
def test_target_dim_lands_any_input_on_one_grid(projection):
    """A square layer raster, the source-shaped DEM and an oversized (h5-like)
    grid all project onto the identical shape."""
    want = projected_shape(SF, projection, 600)
    rng = np.random.default_rng(0)
    for shape in [(600, 600), source_shape(600, SF, projection), (1100, 1500), (90, 120)]:
        out = project_grid(rng.random(shape, dtype=np.float32), *SF, projection, True,
                           target_dim=600)
        assert out.shape == want, (shape, out.shape)
    cat = project_grid(np.ones((600, 600), np.float32), *SF, projection, True,
                       categorical=True, target_dim=600)
    assert cat.shape == want


@pytest.mark.parametrize("projection", ["none", "cosine", "mercator"])
def test_target_dim_keeps_layers_aligned(projection):
    """A feature at a fixed longitude and latitude lands on the same output
    pixel whatever size the layer was rasterised at."""
    north, south, east, west = SF
    lon0 = west + 0.37 * (east - west)
    lat0 = south + 0.61 * (north - south)

    def raster(shape):
        # Two steps (east of lon0, south of lat0): an edge survives any resampling,
        # where a one-pixel line can fall between the samples of a shrink.
        h, w = shape
        lons = west + (np.arange(w) / (w - 1)) * (east - west)
        lats = north - (np.arange(h) / (h - 1)) * (north - south)
        return ((lons[None, :] >= lon0) * 1.0 + (lats[:, None] <= lat0) * 2.0).astype(np.float32)

    peaks = []
    for shape in [(600, 600), source_shape(600, SF, projection), (1082, 1518)]:
        out = np.nan_to_num(project_grid(raster(shape), *SF, projection, True,
                                         target_dim=600))
        east_of = (out % 2) >= 0.5          # the lon step (value 1 or 3)
        south_of = out >= 1.5               # the lat step (value 2 or 3)
        peaks.append((int(np.argmax(south_of.mean(1) > 0.5)),
                      int(np.argmax(east_of.mean(0) > 0.5))))
    rows, cols = zip(*peaks, strict=True)
    assert max(rows) - min(rows) <= 1, peaks
    assert max(cols) - min(cols) <= 1, peaks


def test_cosine_output_has_no_fill():
    """Cosine and the cylindrical family cover the whole output rectangle."""
    out = project_grid(np.ones(source_shape(600, SF, "cosine"), np.float32), *SF,
                       "cosine", True, target_dim=600)
    assert not np.isnan(out).any()


def test_dem_cache_key_tracks_sampling_size():
    from app.server.core.dem_cache import dem_cache_key
    base = {"dim": 600, "dem_source": "h5_local"}
    k_none = dem_cache_key(*SF, {**base, "projection": "none"})
    k_cos = dem_cache_key(*SF, {**base, "projection": "cosine"})
    k_eq = dem_cache_key(*SF, {**base, "projection": "equidistant"})
    assert k_none != k_cos            # 600 vs 759 px raw grids
    assert k_cos == k_eq              # same raw grid, projection applied after
    assert dem_cache_key(*SF, base) == k_none   # projection defaults to none


# ---------------------------------------------------------------------------
# Routes (TEST_MODE): every aligned layer comes back on the DEM's grid
# ---------------------------------------------------------------------------

def _q(projection, **extra):
    n, s, e, w = SF
    q = {"north": n, "south": s, "east": e, "west": w, "dim": 600,
         "projection": projection, "clip_valid_region": "true"}
    q.update(extra)
    return q


@pytest.mark.parametrize("projection", ["none", "cosine", "mercator"])
def test_routes_share_the_dem_grid(client, projection):
    want = list(projected_shape(SF, projection, 600))
    dem = client.get("/api/terrain/dem", params=_q(projection)).json()
    assert dem["dimensions"] == want

    wm = client.get("/api/terrain/water-mask", params=_q(projection)).json()
    assert wm["water_mask_dimensions"] == want
    assert wm["esa_dimensions"] == want
    esa = client.get("/api/terrain/esa-land-cover", params=_q(projection)).json()
    assert esa["esa_dimensions"] == want
    hyd = client.get("/api/terrain/hydrology", params=_q(projection)).json()
    assert hyd["river_grid_dimensions"] == want
    trails = client.get("/api/terrain/trails", params=_q(projection)).json()
    assert trails["grid_dimensions"] == want
    borders = client.get("/api/terrain/borders", params=_q(projection)).json()
    assert borders["grid_dimensions"] == want

    from PIL import Image
    sat = client.get("/api/terrain/satellite", params=_q(projection)).json()
    with Image.open(io.BytesIO(base64.b64decode(sat["image"]))) as img:
        assert [img.size[1], img.size[0]] == want


def test_export_cache_fallback_matches_route(client):
    """resolve_dem's settings-only path rebuilds the route's exact grid."""
    from app.server.core.export_params import resolve_dem
    dem = client.get("/api/terrain/dem", params=_q("cosine")).json()
    n, s, e, w = SF
    got = resolve_dem({"bbox": {"north": n, "south": s, "east": e, "west": w},
                       "dem": {"dim": 600, "projection": "cosine",
                               "dem_source": "local"}})
    assert got is not None
    assert [got[1], got[2]] == dem["dimensions"] == [541, 600]
