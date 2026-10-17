"""geo2stl.projections::_sample_grid against the scipy map_coordinates path it replaced.

F-SPEED #8 (docs/plans/active/F-SPEED-region-open-and-fetch.md): the warping
projections (mercator, lambert, sinusoidal, miller, gall) sampled with a float64
``ndimage.map_coordinates`` on every request. ``_sample_grid`` does the same
separably in float32. These tests pin the contract:

- NaN masks are identical (NaN regions, NaN at the edges, zero-weight NaN neighbours);
- order 0 values are exact;
- order 1 values differ by float32 rounding only: at most ``F32_ULPS`` float32 ulps
  of the largest input magnitude;
- out-of-range coordinates give ``fill_value``.
"""
import numpy as np
import pytest
from scipy import ndimage

from geo2stl import projections
from geo2stl.projections import _sample_grid, project_coordinates

# Two float32 lerps (rows, then columns) round at most a few times each.
F32_ULPS = 4


def _reference(mat, rows, cols, order, fill_value):
    """The pre-#8 sampling: float64 map_coordinates over a full coordinate grid."""
    rows = np.asarray(rows, dtype=np.float64)
    cols = np.asarray(cols, dtype=np.float64)
    if cols.ndim == 1:
        col_grid, row_grid = np.meshgrid(cols, rows)
    else:
        row_grid = np.broadcast_to(rows[:, None], cols.shape)
        col_grid = cols
    return ndimage.map_coordinates(
        mat.astype(np.float64), [row_grid.ravel(), col_grid.ravel()],
        order=order, mode='constant', cval=fill_value,
    ).reshape(row_grid.shape)


def _tol(mat):
    return F32_ULPS * np.finfo(np.float32).eps * max(float(np.nanmax(np.abs(mat))), 1.0)


def _assert_match(new, ref, mat, order):
    assert new.shape == ref.shape
    assert new.dtype == np.float64
    np.testing.assert_array_equal(np.isnan(new), np.isnan(ref))
    ok = ~np.isnan(ref)
    if order == 0:
        np.testing.assert_array_equal(new[ok], ref[ok])
    else:
        assert np.max(np.abs(new[ok] - ref[ok]), initial=0.0) <= _tol(mat)


def _dem(rng, m, n, nan_frac=0.0):
    """Smooth relief plus noise, in metres, with NaN blobs and NaN edge rows/cols."""
    y, x = np.mgrid[0:m, 0:n]
    dem = (1200 * np.sin(x / 17.0) * np.cos(y / 23.0) + 1500
           + rng.normal(0, 30, (m, n))).astype(np.float32)
    if nan_frac:
        dem[rng.random((m, n)) < nan_frac] = np.nan
        dem[m // 3:m // 3 + 5, n // 4:n // 2] = np.nan
        dem[-2, : n // 3] = np.nan      # second-to-last row: reaches row m-1 at x == m-1
        dem[: m // 3, -2] = np.nan      # same for the last column
        dem[0, n // 2:] = np.nan
        dem[m // 2:, 0] = np.nan
    return dem


@pytest.mark.parametrize('order', [0, 1])
@pytest.mark.parametrize('nan_frac', [0.0, 0.01])
@pytest.mark.parametrize('seed', [0, 1, 2])
def test_separable_matches_map_coordinates(order, nan_frac, seed):
    rng = np.random.default_rng(seed)
    m, n = 67, 91
    dem = _dem(rng, m, n, nan_frac)
    # Warped, in-range coordinates hitting both ends exactly, plus integer and
    # half-integer positions (order 0 ties round up, as in scipy).
    rows = np.sort(np.concatenate([
        rng.uniform(0, m - 1, 120), [0, m - 1, m - 1, 1, 2.5, 10.5, m - 1.5]]))
    cols = np.concatenate([rng.uniform(0, n - 1, 150), [0, n - 1, 3, 4.5, n - 1.5]])
    new = _sample_grid(dem, rows, cols, order, np.nan)
    _assert_match(new, _reference(dem, rows, cols, order, np.nan), dem, order)


@pytest.mark.parametrize('order', [0, 1])
def test_per_row_columns_match(order):
    """Sinusoidal passes a 2-D column grid (one row of columns per output row)."""
    rng = np.random.default_rng(5)
    dem = _dem(rng, 50, 70, nan_frac=0.02)
    rows = np.linspace(0, 49, 40)
    cols = rng.uniform(0, 69, (40, 60))
    cols[:, 0], cols[:, -1] = 0, 69
    new = _sample_grid(dem, rows, cols, order, np.nan)
    _assert_match(new, _reference(dem, rows, cols, order, np.nan), dem, order)


@pytest.mark.parametrize('order', [0, 1])
@pytest.mark.parametrize('fill', [np.nan, 0.0, -7.5])
def test_out_of_range_gives_fill(order, fill):
    rng = np.random.default_rng(7)
    dem = _dem(rng, 30, 40, nan_frac=0.02)
    # Overshoots by 1e-12 already count as outside in scipy's 'constant' mode.
    rows = np.array([-1.0, -1e-12, 0, 14.3, 29, 29 + 1e-12, 29.4, 31, np.nan])
    cols = np.array([-0.6, -1e-12, 0, 20.5, 39, 39 + 1e-9, 39.4, 45])
    new = _sample_grid(dem, rows, cols, order, fill)
    _assert_match(new, _reference(dem, rows, cols, order, fill), dem, order)


@pytest.mark.parametrize('order', [0, 1])
@pytest.mark.parametrize('shape', [(1, 9), (9, 1), (1, 1), (2, 2)])
def test_degenerate_shapes(order, shape):
    dem = np.arange(np.prod(shape), dtype=np.float32).reshape(shape) * 3.5
    rows = np.linspace(0, shape[0] - 1, 5)
    cols = np.linspace(0, shape[1] - 1, 6)
    for c in (cols, np.tile(cols, (len(rows), 1))):      # shared and per-row columns
        new = _sample_grid(dem, rows, c, order, np.nan)
        _assert_match(new, _reference(dem, rows, c, order, np.nan), dem, order)


def test_integer_input_order0_is_exact():
    """Class rasters (ESA codes, 0/1 masks) may arrive as uint8."""
    rng = np.random.default_rng(3)
    cls = rng.integers(0, 101, (40, 50)).astype(np.uint8)
    rows, cols = rng.uniform(0, 39, 33), rng.uniform(0, 49, 44)
    new = _sample_grid(cls, rows, cols, 0, 0.0)
    _assert_match(new, _reference(cls, rows, cols, 0, 0.0), cls, 0)


_BBOX = (37.82, 37.66, -122.35, -122.52)
_BBOX_HIGH = (61.2, 59.4, 11.0, 9.0)


@pytest.mark.parametrize('projection', ['mercator', 'lambert', 'sinusoidal', 'miller', 'gall'])
@pytest.mark.parametrize('bbox', [_BBOX, _BBOX_HIGH])
@pytest.mark.parametrize('order', [0, 1])
@pytest.mark.parametrize('maintain', [False, True])
def test_projections_match_old_sampler(monkeypatch, projection, bbox, order, maintain):
    """Every warping projection, end to end, against the map_coordinates sampler."""
    dem = _dem(np.random.default_rng(11), 120, 150, nan_frac=0.01)
    kw = dict(projection=projection, maintain_dimensions=maintain,
              fill_value=np.nan, order=order)
    new, _ = project_coordinates(dem, bbox, **kw)
    monkeypatch.setattr(projections, '_sample_grid', _reference)
    ref, _ = project_coordinates(dem, bbox, **kw)
    _assert_match(new, ref, dem, order)
