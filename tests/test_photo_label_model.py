"""tools/photo_label/model_glb.py: the city model taken from print mm back to metres at its
true place (east-north-up at the bbox centre), on a tiny synthetic DEM and one building."""

import importlib.util
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "photo_label"
if str(TOOL) not in sys.path:
    sys.path.insert(0, str(TOOL))
import model_glb as mg  # noqa: E402

LAT0, LON0 = 37.17, -3.60
M_LAT = 111_320.0
M_LON = M_LAT * math.cos(math.radians(LAT0))
SLOPE = 0.02      # metres up per metre east
H_BUILDING = 15.0


def _ll(e, n):
    return LON0 + e / M_LON, LAT0 + n / M_LAT


def _ground(e):
    return 100.0 + SLOPE * e


@pytest.fixture(scope="module")
def dem(tmp_path_factory):
    """A 1.2 km square GeoTIFF around (LON0, LAT0) sloping up to the east."""
    import rasterio
    from rasterio.transform import from_bounds

    from city2stl.skyline import photo_view

    n = 120
    half_e, half_n = 600.0 / M_LON, 600.0 / M_LAT
    w, s, e, nn = LON0 - half_e, LAT0 - half_n, LON0 + half_e, LAT0 + half_n
    lon = w + (np.arange(n) + 0.5) / n * (e - w)
    east_m = (lon - LON0) * M_LON
    z = np.tile(_ground(east_m), (n, 1)).astype("float32")
    path = tmp_path_factory.mktemp("dem") / "slope.tif"
    with rasterio.open(path, "w", driver="GTiff", width=n, height=n, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_bounds(w, s, e, nn, n, n)) as dst:
        dst.write(z, 1)
    return photo_view.Dem(path)


def _building_fc(e0=200.0, n0=100.0, half=15.0):
    ring = [list(_ll(e0 + de, n0 + dn)) for de, dn in
            [(-half, -half), (half, -half), (half, half), (-half, half), (-half, -half)]]
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {"osm_id": "1", "building": "yes", "height_m": H_BUILDING,
                       "height_source": "osm_height"}}]}


def _server_fc():
    """The same building as the server's footprints give it (``height`` tag, no ``height_m``)."""
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "geometry": _building_fc()["features"][0]["geometry"],
        "properties": {"id": 7, "name": "Torre", "height": "15"}}]}


def test_enu_axes_and_scale():
    """1 km north / east on the WGS84 ellipsoid comes out as 1000 m N / E; height is U."""
    phi = math.radians(LAT0)
    w = math.sqrt(1 - mg.WGS84_E2 * math.sin(phi) ** 2)
    m_merid = mg.WGS84_A * (1 - mg.WGS84_E2) / w ** 3          # metres per radian of latitude
    n_prime = mg.WGS84_A / w                                   # prime vertical radius
    dlat = math.degrees(1000.0 / m_merid)
    dlon = math.degrees(1000.0 / (n_prime * math.cos(phi)))
    e, n, u = mg.lonlath_to_enu([LON0, LON0, LON0, LON0 + dlon], [LAT0, LAT0 + dlat, LAT0, LAT0],
                                [0.0, 0.0, 100.0, 0.0], LON0, LAT0).T
    assert (e[0], n[0], u[0]) == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)
    assert n[1] == pytest.approx(1000.0, abs=0.05) and abs(e[1]) < 1e-6
    assert -0.2 < u[1] < 0                                     # curvature: 1000^2 / 2R = 0.08 m
    assert (e[2], n[2], u[2]) == pytest.approx((0.0, 0.0, 100.0), abs=1e-6)
    assert e[3] == pytest.approx(1000.0, abs=0.05) and abs(n[3]) < 0.1


def test_model_mm_inverts_to_lonlat_and_height():
    """``model_to_lonlath`` undoes ``Terrain.project`` and ``ModelScale.z_mm`` exactly."""
    import shapely

    from city2stl.city_model import Terrain, choose_scale

    bbox = {"north": LAT0 + 0.01, "south": LAT0 - 0.01, "east": LON0 + 0.012, "west": LON0 - 0.012}
    shape = (80, 100)
    scale = choose_scale(bbox, shape, 90.0, 300.0, mm_per_px=1.0, z_mode="true",
                         exaggeration=1.0, base_mm=5.0)
    pts = np.array([[LON0, LAT0], [LON0 + 0.005, LAT0 - 0.007]])
    xy = shapely.get_coordinates(Terrain(np.zeros(shape), bbox, scale).project(shapely.points(pts)))
    z_mm = scale.z_mm(np.array([120.0, 250.0]))
    llh = mg.model_to_lonlath(np.column_stack([xy, z_mm]), bbox, shape, scale)
    assert np.allclose(llh[:, :2], pts, atol=1e-10)
    assert np.allclose(llh[:, 2], [120.0, 250.0], atol=1e-9)


def test_built_model_sits_at_true_place_and_height(dem):
    bbox = mg.clip_box(mg.dem_bounds(dem), 1.0)
    model, shape = mg.build_model(dem, {"buildings": _building_fc()}, bbox, grid_px=100)
    lon0 = (bbox["east"] + bbox["west"]) / 2
    lat0 = (bbox["north"] + bbox["south"]) / 2
    b = mg.model_to_enu(model.parts["buildings"].vertices, bbox, shape, model.scale, lon0, lat0)
    e_c, n_c = (b[:, 0].min() + b[:, 0].max()) / 2, (b[:, 1].min() + b[:, 1].max()) / 2
    assert e_c == pytest.approx(200.0, abs=6.0) and n_c == pytest.approx(100.0, abs=6.0)
    assert b[:, 0].max() - b[:, 0].min() == pytest.approx(30.0, abs=6.0)   # true metric size
    top = b[:, 2].max()
    assert top == pytest.approx(_ground(200.0) + H_BUILDING, abs=1.5)
    t = mg.model_to_enu(model.parts["terrain"].vertices, bbox, shape, model.scale, lon0, lat0)
    surf = t[t[:, 2] > 95.0]                       # the top surface, not the base slab
    fit = np.polyfit(surf[:, 0], surf[:, 2], 1)
    assert fit[0] == pytest.approx(SLOPE, abs=2e-3) and fit[1] == pytest.approx(100.0, abs=1.0)


def test_build_or_load_writes_glb_once(dem, tmp_path, monkeypatch):
    import trimesh

    monkeypatch.setattr(mg, "find_city_payload", lambda bounds: None)
    meta = mg.build_or_load(dem, dem.path, "test", tmp_path, footprints=_server_fc,
                            log=lambda *_: None, grid_px=80)
    glb = Path(meta["glb_path"])
    assert glb.is_file() and meta["source"] == "footprints" and meta["glb_bytes"] == glb.stat().st_size
    assert json.loads(glb.with_suffix(".json").read_text(encoding="utf-8"))["faces"] == meta["faces"]
    assert meta["ground"] and all(len(g) == 3 for g in meta["ground"])
    scene = trimesh.load(glb, force="scene")
    assert "buildings" in scene.geometry and "terrain" in scene.geometry
    v = scene.geometry["buildings"].vertices        # stored Z up, no axis swap applied by us
    assert v[:, 2].max() == pytest.approx(_ground(200.0) + H_BUILDING, abs=1.5)
    mtime = glb.stat().st_mtime_ns
    again = mg.build_or_load(dem, dem.path, "test", tmp_path, footprints=_server_fc,
                             log=lambda *_: None, grid_px=80)
    assert again["glb_path"] == meta["glb_path"] and glb.stat().st_mtime_ns == mtime


def test_payload_layers_keeps_feature_collections_only():
    payload = {"buildings": _building_fc(), "roads": {"type": "FeatureCollection", "features": []},
               "cache_key": "abc", "survey_fill": {"n": 3}}
    assert set(mg.payload_layers(payload)) == {"buildings", "roads"}


def test_server_model_route_reports_missing_dem(tmp_path):
    spec = importlib.util.spec_from_file_location("photo_label_server_m", TOOL / "server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    osm = tmp_path / "osm.json"
    osm.write_text(json.dumps({"elements": []}), encoding="utf-8")
    cfg = srv.Config(tmp_path, osm, tmp_path, "t", api_key="", dem_path=None, model_dir=tmp_path / "m")
    assert srv.api_model_info(cfg)["status"] == "building"
    deadline = time.time() + 10
    while srv.api_model_info(cfg)["status"] == "building" and time.time() < deadline:
        time.sleep(0.05)
    state = srv.api_model_info(cfg)
    assert state["status"] == "error" and "DEM" in state["error"]
    assert not (tmp_path / "m").exists()
