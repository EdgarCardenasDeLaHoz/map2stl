"""Projected shapes checked against hand-computed map-projection maths.

The other projection tests compare outputs with ``expected_aspect_ratio``, which shares the
formulas under test, so a wrong formula passes them. These use the textbook definitions
directly. For a small box the local width/height ratio is
(dx/dlambda) / (dy/dphi) * (lon span / lat span). Found 2026-10-02: Gall was sqrt(2) too wide
and sinusoidal 20 % too narrow at 37° N.
"""
import numpy as np
import pytest

from geo2stl.projections import project_coordinates

# Granada, 2 km box (lon span / lat span = 1.2556)
BOX = (37.1873, 37.1693, -3.5867, -3.6093)
LAT = np.radians((BOX[0] + BOX[1]) / 2)
SPAN = (BOX[2] - BOX[3]) / (BOX[0] - BOX[1])

EXPECTED = {
    # name: (dx/dlambda) / (dy/dphi) at the box's latitude
    "none": 1.0,
    "cosine": np.cos(LAT),
    "equidistant": np.cos(LAT),
    "mercator": np.cos(LAT),                                       # 1 / sec(phi)
    "lambert": 1.0 / np.cos(LAT),                                  # y = sin(phi)
    "sinusoidal": np.cos(LAT),                                     # x = lambda cos(phi)
    "miller": np.cos(0.8 * LAT),                                   # y' = sec(0.8 phi)
    "gall": (1 / np.sqrt(2)) / ((1 + np.sqrt(2) / 2) / 2 / np.cos(LAT / 2) ** 2),
}


@pytest.mark.parametrize("projection", sorted(EXPECTED))
def test_small_box_aspect_matches_the_projection(projection):
    m = 320
    n = int(round(m * SPAN))                 # equal-angle input grid, as the DEM fetch makes
    mat = np.ones((m, n), dtype=np.float32)
    out, _ = project_coordinates(mat, BOX, projection, clip_nans=True, maintain_dimensions=False)
    got = out.shape[1] / out.shape[0]
    want = SPAN * EXPECTED[projection]
    assert got == pytest.approx(want, rel=0.02), f"{projection}: {got:.3f} vs {want:.3f}"
