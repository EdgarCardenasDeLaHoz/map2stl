"""Landmark listing and overrides (city2stl/landmarks.py, F-LANDMARK §3/§5)."""

import math

import numpy as np
import pytest
import shapely
import trimesh
from rasterio.transform import Affine
from shapely.geometry import Polygon, box

from city2stl import landmarks as lm
from city2stl.city_model import build_city_model, build_on_terrain, choose_scale

BBOX = dict(north=37.1780, south=37.1740, east=-3.5960, west=-3.6010)   # ~445 x 445 m, flat; square DEM pixels at 150 x 150


def _rect(lon, lat, dlon, dlat):
    return {"type": "Polygon", "coordinates": [[[lon - dlon, lat - dlat], [lon + dlon, lat - dlat],
                                                 [lon + dlon, lat + dlat], [lon - dlon, lat + dlat],
                                                 [lon - dlon, lat - dlat]]]}


def _fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


C_LON, C_LAT = -3.5985, 37.1760
CATHEDRAL = {"geometry": _rect(C_LON, C_LAT, 0.0004, 0.0002),
             "properties": {"osm_id": "way/1", "name": "Catedral", "building": "cathedral",
                            "amenity": "place_of_worship", "height_m": 40.0,
                            "height_source": "osm_tag"}}
TOWER = {"geometry": _rect(C_LON + 0.0003, C_LAT, 0.00005, 0.00005),
         "properties": {"osm_id": "way/2", "building:part": "yes", "height_m": 57.0,
                        "roof:shape": "pyramidal"}}
NAVE = {"geometry": _rect(C_LON - 0.0001, C_LAT, 0.0002, 0.0001),
        "properties": {"osm_id": "way/3", "building:part": "yes", "height_m": 30.0,
                       "roof:shape": "gabled"}}
TOWNHALL = {"geometry": _rect(-3.6000, 37.1770, 0.0001, 0.0001),
            "properties": {"osm_id": "way/4", "amenity": "townhall", "height_m": 18.0}}
TALL = {"geometry": _rect(-3.5970, 37.1750, 0.0001, 0.0001),
        "properties": {"osm_id": "way/5", "building": "yes", "height_m": 25.0}}
SHORT = {"geometry": _rect(-3.5975, 37.1775, 0.0001, 0.0001),
         "properties": {"osm_id": "way/6", "building": "yes", "height_m": 6.0}}
BUILDINGS = _fc(CATHEDRAL, TOWER, NAVE, TOWNHALL, TALL, SHORT)


def _flat_scale(shape=(150, 150)):
    return choose_scale(BBOX, shape, 500.0, 500.0, mm_per_px=1.0, z_mode="true", base_mm=3.0)


def _build(buildings, overrides, layers=None, shape=(150, 150)):
    scale = _flat_scale(shape)
    z = scale.z_mm(np.full(shape, 500.0))
    return build_on_terrain(z, BBOX, scale, {"buildings": buildings, **(layers or {})},
                            layer_overrides={"buildings": {"max_slenderness": 0}},
                            landmark_overrides=overrides)


class TestListing:
    def test_categories_parts_and_tallest(self):
        items = lm.list_landmarks(BUILDINGS["features"], tallest_n=1)
        by_id = {i["osm_id"]: i for i in items}
        assert set(by_id) == {"way/1", "way/4", "way/5"}      # parts not listed; way/6 not tallest
        cat = by_id["way/1"]
        assert cat["category"] == "worship"
        assert cat["parts"] == 2
        assert cat["height_m"] == 57.0                        # tallest part
        assert cat["roof_shapes"] == ["flat", "gabled", "pyramidal"]
        assert by_id["way/4"]["category"] == "civic"
        assert by_id["way/5"]["category"] == "tallest"
        assert [i["category"] for i in items] == ["worship", "civic", "tallest"]
        assert cat["area_m2"] == pytest.approx(0.0008 * 0.0004 * 111_320**2
                                               * math.cos(math.radians(C_LAT)), rel=0.02)

    def test_idless_copy_from_another_layer_is_dropped(self):
        copy = {"geometry": CATHEDRAL["geometry"],
                "properties": {"name": "Catedral", "amenity": "place_of_worship", "height_m": 40}}
        items = lm.list_landmarks(BUILDINGS["features"] + [copy], tallest_n=0)
        assert [i["osm_id"] for i in items] == ["way/1", "way/4"]

    @pytest.mark.parametrize("tags,cat", [
        ({"historic": "castle"}, "historic"), ({"building": "palace"}, "historic"),
        ({"tourism": "museum"}, "attraction"), ({"building": "government"}, "civic"),
        ({"building": "mosque"}, "worship"), ({"building": "yes"}, None),
        ({"amenity": float("nan")}, None)])
    def test_category(self, tags, cat):
        assert lm.landmark_category(tags) == cat

    def test_landmark_features_takes_parts_inside(self):
        got = lm.landmark_features(BUILDINGS["features"], "way/1")
        assert [f["properties"]["osm_id"] for f in got] == ["way/1", "way/2", "way/3"]
        assert lm.landmark_features(BUILDINGS["features"], "way/99") == []


class TestFit:
    def test_rectangle_fit_matches_rotated_footprint(self):
        m = shapely.affinity.rotate(box(0, 0, 10, 4), 30, origin=(0, 0))
        xy = np.asarray(m.exterior.coords)[:-1]
        fp = shapely.affinity.rotate(box(100, 50, 160, 70), 70, origin="centroid")
        out, k = lm.fit_mesh_xy(xy, fp)
        placed = Polygon(out).convex_hull
        assert placed.intersection(fp).area / placed.union(fp).area > 0.999
        assert k == pytest.approx(math.sqrt(6 * 5))

    def test_asymmetric_mesh_picks_orientation_by_overlap(self):
        # An L-ish outline: the 180 deg flip overlaps an L footprint worse.
        l_mesh = np.array([[0, 0], [10, 0], [10, 2], [2, 2], [2, 6], [0, 6]], float)
        fp = Polygon([(0, 0), (20, 0), (20, 4), (4, 4), (4, 12), (0, 12)])
        out, _ = lm.fit_mesh_xy(l_mesh, fp)
        hull = Polygon(out)
        assert hull.intersection(fp).area / hull.union(fp).area > 0.95

    def test_manual_rotation_scale_offset(self):
        xy = np.array([[0, 0], [4, 0], [4, 2], [0, 2]], float)
        fp = box(0, 0, 8, 4)
        base, _ = lm.fit_mesh_xy(xy, fp)
        moved, k = lm.fit_mesh_xy(xy, fp, rotation_deg=90, scale=0.5, offset_mm=(1, 2))
        c = np.array([4.0, 2.0])
        expect = (np.array([[0, -1], [1, 0]]) @ ((base - c) * 0.5).T).T + c + [1, 2]
        np.testing.assert_allclose(moved, expect, atol=1e-9)
        assert k == pytest.approx(1.0)


class TestLoadMesh:
    def test_repairs_split_vertices(self, tmp_path):
        b = trimesh.creation.box((4, 2, 3))
        soup = trimesh.Trimesh(b.triangles.reshape(-1, 3), np.arange(36).reshape(-1, 3), process=False)
        p = tmp_path / "soup.stl"
        soup.export(p)
        m = lm.load_mesh(p)
        assert m.is_watertight and m.volume == pytest.approx(24.0)

    def test_rejects_non_manifold(self, tmp_path):
        # Three fins on one edge: no repair makes that a solid.
        v = np.array([[0, 0, 0], [0, 0, 1], [1, 0, 0], [-1, 0, 0], [0, 1, 0]], float)
        f = np.array([[0, 1, 2], [0, 1, 3], [0, 1, 4]])
        p = tmp_path / "fins.obj"
        trimesh.Trimesh(v, f, process=False).export(p)
        with pytest.raises(lm.LandmarkError, match="not a closed solid"):
            lm.load_mesh(p)

    def test_unreadable_file(self, tmp_path):
        p = tmp_path / "junk.stl"
        p.write_bytes(b"not a mesh")
        with pytest.raises(lm.LandmarkError):
            lm.load_mesh(p)


class TestMeshOverride:
    def _override(self, **spec):
        mesh = trimesh.creation.box((8.0, 4.0, 2.0))       # 8 x 4 units, 2 tall
        return {"way/1": lm.LandmarkOverride("way/1", "mesh", spec, mesh=mesh, source="mesh")}

    def test_replaces_outline_and_parts(self):
        model = _build(BUILDINGS, self._override())
        rep = model.report["landmarks"]["way/1"]
        assert rep["status"] == "applied" and rep["replaced_features"] == 3
        assert model.report["merged"]["watertight"]
        b = model.parts["buildings"]
        # Tallest thing left is the 25 m building (way/5); the 57 m tower is gone.
        mm_per_m = model.scale.z_mm_per_m
        assert b.bounds[1, 2] == pytest.approx(3.0 + 25.0 * mm_per_m, abs=0.05)

    def test_true_scale_height_follows_horizontal_fit(self):
        scale = _flat_scale()
        model = _build(_fc(CATHEDRAL), self._override())
        # Footprint 0.0008 deg lon x 0.0004 deg lat on an 8 x 4 unit mesh: the vertical
        # takes the geometric mean of the two horizontal stretches.
        m_per_unit = math.sqrt(0.0008 * 111_320 * math.cos(math.radians(C_LAT)) / 8
                               * 0.0004 * 111_320 / 4)
        expect = 3.0 + 2.0 * m_per_unit * scale.z_mm_per_m
        assert model.merged.bounds[1, 2] == pytest.approx(expect, rel=0.03)

    def test_vertical_fit_to_height(self):
        scale = _flat_scale()
        model = _build(_fc(CATHEDRAL), self._override(vertical="fit", height_m=30))
        assert model.merged.bounds[1, 2] == pytest.approx(3.0 + 30 * scale.z_mm_per_m, abs=0.1)

    def test_gltf_is_y_up(self):
        mesh = trimesh.creation.box((8.0, 6.0, 4.0))        # tall axis = y (6)
        ov = {"way/1": lm.LandmarkOverride("way/1", "mesh", {"format": "glb", "vertical": "fit",
                                                             "height_m": 20}, mesh=mesh)}
        scale = _flat_scale()
        model = _build(_fc(CATHEDRAL), ov)
        assert model.merged.bounds[1, 2] == pytest.approx(3.0 + 20 * scale.z_mm_per_m, abs=0.1)

    def test_drops_duplicate_in_churches_layer(self):
        churches = _fc({"geometry": CATHEDRAL["geometry"], "properties": {"height_m": 40}})
        model = _build(_fc(CATHEDRAL), self._override(), layers={"churches": churches})
        assert "churches" not in model.parts
        assert model.report["layers"]["churches"]["landmark_replaced"] == 1

    def test_unknown_id_is_reported(self):
        model = _build(_fc(TALL), self._override())
        assert model.report["landmarks"]["way/1"]["status"] == "missing"


def _gable_ndsm(eave=20.0, ridge=30.0, res_deg=0.00001):
    """A gable along longitude over the cathedral footprint, ground (0) around it."""
    n, s, e, w = C_LAT + 0.0003, C_LAT - 0.0003, C_LON + 0.0005, C_LON - 0.0005
    h, wd = round((n - s) / res_deg), round((e - w) / res_deg)
    t = Affine((e - w) / wd, 0, w, 0, -(n - s) / h, n)
    cols, rows = np.meshgrid(np.arange(wd) + 0.5, np.arange(h) + 0.5)
    lon, lat = t * (cols, rows)
    inside = (np.abs(lon - C_LON) <= 0.0004) & (np.abs(lat - C_LAT) <= 0.0002)
    roof = ridge - (ridge - eave) * np.abs(lat - C_LAT) / 0.0002
    return np.where(inside, roof, 0.0).astype(np.float32), t


class TestNdsmOverride:
    def test_gable_from_ndsm(self):
        arr, t = _gable_ndsm()
        ov = {"way/1": lm.LandmarkOverride("way/1", "ndsm", {}, ndsm=arr, transform=t, source="x")}
        model = _build(BUILDINGS, ov)
        assert model.report["landmarks"]["way/1"]["status"] == "applied"
        assert model.report["merged"]["watertight"]
        zpm = model.scale.z_mm_per_m
        b = model.parts["buildings"]
        assert b.bounds[1, 2] == pytest.approx(3.0 + 30 * zpm, abs=0.3)
        # Clipped exactly to the footprint: no wider than it.
        fp = shapely.geometry.shape(CATHEDRAL["geometry"])
        from city2stl.city_model import Terrain
        terr = Terrain(np.zeros((150, 150)), BBOX, model.scale)
        x0, y0, x1, y1 = terr.project(np.array([fp]))[0].bounds
        cat = b.split(only_watertight=False)
        widest = max(cat, key=lambda m: m.extents[0])
        assert widest.bounds[0, 0] >= x0 - 0.02 and widest.bounds[1, 0] <= x1 + 0.02

    def test_low_coverage_falls_back_to_osm(self):
        arr, t = _gable_ndsm()
        arr[:] = np.nan
        arr[0, 0] = 5.0
        ov = {"way/1": lm.LandmarkOverride("way/1", "ndsm", {}, ndsm=arr, transform=t, source="x")}
        model = _build(BUILDINGS, ov)
        rep = model.report["landmarks"]["way/1"]
        assert rep["status"] == "failed" and "cover" in rep["reason"]
        # The OSM tower (57 m, pyramidal) is still there.
        assert model.parts["buildings"].bounds[1, 2] > 3.0 + 45 * model.scale.z_mm_per_m


class TestResolve:
    def test_kinds_and_errors(self, tmp_path):
        feats = BUILDINGS["features"]
        assert lm.resolve_overrides({"way/1": {"kind": "osm"}}, feats) == {}
        with pytest.raises(lm.LandmarkError, match="unknown override kind"):
            lm.resolve_overrides({"way/1": {"kind": "lego"}}, feats)
        with pytest.raises(lm.LandmarkError, match="way/99"):
            lm.resolve_overrides({"way/99": {"kind": "ndsm"}}, feats)
        with pytest.raises(lm.LandmarkError, match="upload_id"):
            lm.resolve_overrides({"way/1": {"kind": "mesh"}}, feats)

    def test_mesh_by_upload(self, tmp_path):
        p = tmp_path / "m.stl"
        trimesh.creation.box((2, 2, 2)).export(p)
        got = lm.resolve_overrides({"way/1": {"kind": "mesh", "upload_id": "u"}},
                                   BUILDINGS["features"], mesh_path=lambda uid: p)
        assert got["way/1"].mesh.is_watertight and got["way/1"].spec["format"] == "stl"

    def test_ndsm_fetch(self):
        arr, t = _gable_ndsm()
        calls = []

        def fetch(name, bbox, res):
            calls.append((name, bbox, res))
            return None if name == "empty" else (arr, t)

        got = lm.resolve_overrides({"way/1": {"kind": "ndsm", "provider": "p1", "resolution_m": 0.5}},
                                   BUILDINGS["features"], ndsm_fetch=fetch)
        assert got["way/1"].source == "p1" and calls[0][2] == 0.5
        n, s, e, w = calls[0][1]
        assert n > C_LAT + 0.0002 and w < C_LON - 0.0004          # footprint plus margin
        with pytest.raises(lm.LandmarkError, match="no data"):
            lm.resolve_overrides({"way/1": {"kind": "ndsm", "provider": "empty"}},
                                 BUILDINGS["features"], ndsm_fetch=fetch)


def test_build_city_model_passes_overrides():
    mesh = trimesh.creation.box((8.0, 4.0, 2.0))
    ov = {"way/1": {"kind": "mesh", "mesh": mesh}}
    model = build_city_model(np.full((150, 150), 500.0), BBOX, {"buildings": _fc(CATHEDRAL)},
                             z_mode="true", landmark_overrides=ov)
    assert model.report["landmarks"]["way/1"]["status"] == "applied"
