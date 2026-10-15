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


def _old_borders_grid(lines_by_kind, shape):
    """borders_grid before the GeoJSON memo: numpy2stl's burn_polygons on each call."""
    import cv2
    from numpy2stl.raster import burn_polygons

    h, w = shape
    grid = np.zeros((h, w), dtype=np.uint8)
    for kind, code in (("states", STATE), ("countries", COUNTRY)):
        sub = lines_by_kind[kind].cx[W:E, S:N]
        if not len(sub):
            continue
        burned = burn_polygons(list(sub.geometry.to_numpy()), (h, w), bounds=(W, S, E, N),
                               values=1.0, mode="max", dtype=np.uint8)
        if code == COUNTRY:
            near = cv2.dilate((burned > 0).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            grid[near & (grid == STATE)] = 0
        grid[burned > 0] = code
    return grid


def _random_lines(rng, n):
    from shapely.geometry import MultiLineString
    geoms = []
    for i in range(n):
        pts = rng.uniform(-2.0, 12.0, size=(int(rng.integers(2, 8)), 2))
        if i % 7 == 3:                                       # some multi-part borders
            geoms.append(MultiLineString([pts, pts[::-1] + 0.3]))
        else:
            geoms.append(LineString(pts))
    geoms.append(LineString())                               # an empty line is skipped
    geoms.append(LineString([(80.0, 80.0), (81.0, 81.0)]))   # outside the box
    return gpd.GeoDataFrame({"geometry": geoms}, crs="EPSG:4326")


def test_memoised_burn_matches_burn_polygons(monkeypatch):
    """The GeoJSON memo + direct rasterio burn gives the same grid as burn_polygons,
    both cold and from the memo, on the Natural Earth path and the lines_by_kind path."""
    import geo2stl.borders as b

    rng = np.random.default_rng(7)
    data = {"countries": _random_lines(rng, 40), "states": _random_lines(rng, 120)}
    monkeypatch.setattr(b, "_MEMO", {})
    monkeypatch.setattr(b, "_GEOJSON", {})
    for kind, gdf in data.items():
        b._MEMO[kind] = gdf.reset_index(drop=True)
        b._GEOJSON[kind] = np.full(len(gdf), None, dtype=object)
    for shape in ((100, 100), (37, 81)):
        want = _old_borders_grid(data, shape)
        assert want.any()
        for _ in range(2):                                   # cold, then from the memo
            got, _counts = borders_grid(N, S, E, W, shape)
            np.testing.assert_array_equal(got, want)
        got, _counts = borders_grid(N, S, E, W, shape, lines_by_kind=data)
        np.testing.assert_array_equal(got, want)
    memo = b._GEOJSON["states"]
    assert sum(m is not None for m in memo[:120]) > 100      # converted once, kept
    assert memo[120] is None and memo[121] is None           # empty / outside: never converted


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
