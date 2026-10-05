"""Country / state borders grid (geo2stl/borders.py) and GET /api/terrain/borders. No downloads."""
import geopandas as gpd
import numpy as np
from shapely.geometry import LineString

from geo2stl.borders import COUNTRY, STATE, borders_grid

N, S, E, W = 10.0, 0.0, 10.0, 0.0


def _lines(*coords):
    return gpd.GeoDataFrame({"geometry": [LineString(c) for c in coords]}, crs="EPSG:4326")


def test_country_and_state_lines_burn_with_their_codes():
    countries = _lines([(5.0, -1.0), (5.0, 11.0)])           # vertical, through the middle
    states = _lines([(-1.0, 2.5), (11.0, 2.5)])              # horizontal, row near the south
    g, counts = borders_grid(N, S, E, W, (100, 100), lines_by_kind={"countries": countries, "states": states})
    assert counts == {"states": 1, "countries": 1}
    assert (g[:, 49:51] == COUNTRY).any(axis=1).all()          # an unbroken country line
    assert (g == STATE).any(axis=1)[74:76].any()                # the state line, row 0 north
    assert set(np.unique(g)) <= {0, STATE, COUNTRY}


def test_state_line_beside_a_country_border_is_dropped():
    """The two datasets are generalised apart: a state line on a country border is not doubled."""
    countries = _lines([(5.0, -1.0), (5.0, 11.0)])
    states = _lines([(5.12, -1.0), (5.12, 11.0)])            # ~1 px east of the border
    g, _ = borders_grid(N, S, E, W, (100, 100), lines_by_kind={"countries": countries, "states": states})
    assert (g == STATE).sum() == 0
    assert (g == COUNTRY).sum() >= 100


def test_box_without_borders_is_empty():
    far = _lines([(50.0, 50.0), (51.0, 51.0)])
    g, counts = borders_grid(N, S, E, W, (40, 40), lines_by_kind={"countries": far, "states": far})
    assert counts == {"states": 0, "countries": 0}
    assert not g.any()


def test_borders_endpoint_returns_a_coded_grid(client):
    r = client.get("/api/terrain/borders", params={"north": 40.1, "south": 40.0, "east": -74.9,
                                                    "west": -75.0, "dim": 64})
    assert r.status_code == 200
    d = r.json()
    h, w = d["grid_dimensions"]
    assert (h, w) == (64, 64)
    assert d["codes"] == {"state": 1, "country": 2}
    import base64
    vals = np.frombuffer(base64.b64decode(d["grid_values_b64"]), dtype=np.float32)
    assert vals.size == h * w and set(np.unique(vals)) <= {0.0, 1.0, 2.0}
