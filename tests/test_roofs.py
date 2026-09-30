"""city2stl.roofs: pitched roofs reach their height and stay closed solids."""

import numpy as np
import pytest
import trimesh
from numpy2stl.processing.boolean import from_manifold, union
from shapely.geometry import Polygon

from city2stl.roofs import building_solids, convex_pieces

RECT = Polygon([(0, 0), (10, 0), (10, 6), (0, 6)])
ELL = Polygon([(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)])


def _solid(poly, shape, **props):
    u, rejected = union(building_solids(poly, 0.0, 5.0, 3.0, shape, props))
    assert rejected == 0
    v, f = from_manifold(u)
    m = trimesh.Trimesh(v, f)
    assert m.is_watertight and m.volume > 0
    return m


@pytest.mark.parametrize("shape", ["gabled", "hipped", "skillion", "dome", "onion", "cone"])
@pytest.mark.parametrize("poly", [RECT, ELL], ids=["rect", "L"])
def test_every_shape_is_a_closed_solid_up_to_the_roof(poly, shape):
    m = _solid(poly, shape)
    assert m.bounds[1, 2] == pytest.approx(8.0, abs=0.5)


def test_gable_volume_is_walls_plus_prism_roof():
    assert _solid(RECT, "gabled").volume == pytest.approx(10 * 6 * 5 + 10 * 6 * 3 / 2)


def test_hip_is_smaller_than_gable():
    assert _solid(RECT, "hipped").volume < _solid(RECT, "gabled").volume


def test_ridge_runs_along_the_long_axis_unless_across():
    along = _solid(RECT, "gabled")
    ridge = along.vertices[np.isclose(along.vertices[:, 2], 8.0)]
    assert np.ptp(ridge[:, 0]) == pytest.approx(10) and np.ptp(ridge[:, 1]) < 1e-6
    across = _solid(RECT, "gabled", **{"roof:orientation": "across"})
    ridge = across.vertices[np.isclose(across.vertices[:, 2], across.bounds[1, 2])]
    assert np.ptp(ridge[:, 1]) == pytest.approx(6) and np.ptp(ridge[:, 0]) < 1e-6


def test_skillion_falls_toward_roof_direction():
    m = _solid(RECT, "skillion", **{"roof:direction": "90"})     # faces east
    top = m.vertices[m.vertices[:, 2] > 4.99]
    assert top[np.argmax(top[:, 2]), 0] < top[np.argmin(top[:, 2]), 0]


def test_concave_footprint_splits_into_convex_pieces():
    pieces = convex_pieces(ELL)
    assert len(pieces) == 2
    assert sum(p.area for p in pieces) == pytest.approx(ELL.area)


def test_courtyard_building_gets_a_flat_top():
    ring = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(3, 3), (7, 3), (7, 7), (3, 7)]])
    m = _solid(ring, "gabled")
    assert m.bounds[1, 2] == pytest.approx(6.5)


def _check_random_footprints(n):
    """Rotated, scaled and rounded outlines (as OSM gives them) never crash or leak."""
    from shapely import affinity

    rng = np.random.default_rng(7)
    for k in range(n):
        base = [RECT, ELL][k % 2]
        s = rng.uniform(0.05, 3)
        p = affinity.rotate(affinity.scale(base, s * rng.uniform(0.5, 2), s), rng.uniform(0, 180))
        p = Polygon(np.round(np.asarray(affinity.translate(p, 300, 200).exterior.coords), 2))
        if not p.is_valid:
            continue
        for shape in ("pyramidal", "hipped", "gabled", "skillion"):
            u, rejected = union(building_solids(p, 0.0, 5.0, rng.uniform(0.1, 3), shape))
            assert rejected == 0 and u is not None
            v, f = from_manifold(u)
            assert trimesh.Trimesh(v, f, process=False).is_watertight


def test_random_footprints_always_give_closed_solids():
    # 30 of the 120 seeded outlines (the first 30 are the same ones); the full
    # sweep takes ~12 s and runs with -m slow.
    _check_random_footprints(30)


@pytest.mark.slow
def test_random_footprints_always_give_closed_solids_full():
    _check_random_footprints(120)
