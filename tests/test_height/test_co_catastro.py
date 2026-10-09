"""Colombian cadastre floors as per-footprint heights (``providers/co_catastro.py``)."""

import io
import math
import zipfile

import numpy as np
import pytest
from shapely.geometry import box

from city2stl.height.providers import co_catastro as cc

LAT, LON = 10.42, -75.55
M = 1 / 111320.0                      # ~1 m in degrees of latitude


def sq(x_m, y_m, w_m, h_m=None):
    """A w x h metre box, its lower-left corner x/y metres from (LON, LAT)."""
    h_m = w_m if h_m is None else h_m
    k = M / math.cos(math.radians(LAT))
    return box(LON + x_m * k, LAT + y_m * M, LON + (x_m + w_m) * k, LAT + (y_m + h_m) * M)


DS = cc.CadastreDataset(
    name="test", domain="example.invalid", dataset_id="test-0000", member_prefix="SHP_Test/",
    bbox=(10.5, 10.3, -75.4, -75.7), attribution="Test cadastre, CC BY-SA 4.0",
    storey_m_by_zone={"CENTRO": 4.5})

PH = "1300101010000006109109" + "00000691"        # condition digit (22nd) is 9
NPH = "1300101040000064900030" + "00000000"


def layer(rows, zones=("CENTRO",)):
    """rows: (geometry, floors, zone index, codigo)."""
    return cc.CadastreLayer(
        geometry=np.array([r[0] for r in rows], dtype=object),
        floors=np.array([float(r[1]) if 0 < r[1] < cc.MAX_FLOORS else np.nan for r in rows]),
        raw_floors=np.array([r[1] for r in rows]), basements=np.zeros(len(rows), int),
        mezzanines=np.zeros(len(rows), int), zone=np.array([r[2] for r in rows]),
        zones=list(zones), codigo=np.array([r[3] for r in rows], dtype="S30"))


# --------------------------------------------------------------------------- rules

def test_is_ph():
    assert cc.is_ph(PH)
    assert not cc.is_ph(NPH)
    assert not cc.is_ph("")


def test_height_by_zone_and_band():
    assert cc.height_m(DS, 1) == pytest.approx(1 * cc.DEFAULT_STOREY_M + cc.ROOF_M)
    assert cc.height_m(DS, 2, "Centro") == pytest.approx(2 * 4.5 + cc.ROOF_M)
    assert cc.height_m(DS, 2, "CENTRO", commercial=True) == pytest.approx(
        2 * 4.5 + cc.ROOF_M + cc.COMMERCIAL_GROUND_M)
    # towers: the skyline storey calibration, whatever the zone
    assert cc.height_m(DS, 20, "CENTRO") == pytest.approx(20 * cc.TOWER_STOREY_M + cc.TOWER_ROOF_M)
    assert cc.height_m(DS, float("nan")) is None
    assert cc.height_m(DS, 0) is None


def test_confidence_publishable_and_defers():
    assert cc.confidence(3) == cc.CONF_LOW
    assert cc.confidence(8) == cc.CONF_MID
    assert cc.confidence(30) == cc.CONF_TOWER
    assert cc.confidence(3, ph=True) == cc.CONF_PH
    assert cc.publishable(3, False) and cc.publishable(cc.TOWER_FLOORS, False)
    assert not cc.publishable(cc.TOWER_FLOORS + 1, False)
    assert not cc.publishable(3, True)
    assert cc.defers(cc.TOWER_FLOORS + 1) and not cc.defers(cc.TOWER_FLOORS)


def test_clean_drops_duplicates_keeps_max_and_flags_errors():
    g = sq(0, 0, 10)
    keep, floors, ndup = cc.clean([2, 3, 0, 77, 1], [g, g, sq(20, 0, 10), sq(40, 0, 10),
                                                      sq(60, 0, 10)])
    assert keep.tolist() == [True, False, True, True, True]
    assert floors[0] == 3                     # the duplicate's larger count
    assert np.isnan(floors[2]) and np.isnan(floors[3])
    assert ndup == 1


def test_dataset_for_bbox():
    assert cc.dataset_for((10.43, 10.38, -75.52, -75.57)).name == "cartagena"
    assert cc.dataset_for((40.5, 40.4, -3.6, -3.7)) is None
    assert cc.covers((10.43, 10.38, -75.52, -75.57))


# --------------------------------------------------------------------------- matching

def test_match_takes_the_tallest_part_and_its_zone():
    fp = sq(0, 0, 20)
    lay = layer([(sq(0, 0, 20, 14), 1, 0, NPH),       # main house, largest overlap
                 (sq(0, 14, 20, 6), 2, 0, NPH),       # upper part, mostly inside
                 (sq(19, 0, 30), 9, -1, NPH)])        # neighbour, barely touching
    m = cc.match_footprints({"a": fp}, lay, ds=DS)["a"]
    assert m.floors == 2 and m.floors_best == 1 and m.n_parts == 2
    assert m.zone == "CENTRO" and m.storey_m == 4.5
    assert m.height_m == pytest.approx(2 * 4.5 + cc.ROOF_M)
    assert m.iou == pytest.approx(1.0, abs=0.02)
    assert m.publishable and not m.ph and m.conf == cc.CONF_LOW


def test_match_iou_gate_and_invalid_main_polygon():
    lay = layer([(sq(0, 0, 10), 2, -1, NPH),
                 (sq(100, 0, 30), 0, -1, NPH),        # main polygon with an error count
                 (sq(100, 0, 5), 2, -1, NPH)])        # annex inside it
    out = cc.match_footprints({"small_overlap": sq(7, 7, 20),   # IoU ~0.02
                               "bad_main": sq(100, 0, 30)}, lay, ds=DS)
    assert out == {}


def test_match_flags_propiedad_horizontal():
    lay = layer([(sq(0, 0, 25), 1, -1, PH)])           # a tower's PH unit: 1 floor
    m = cc.match_footprints({"t": sq(0, 0, 25)}, lay, ds=DS)["t"]
    assert m.ph and not m.publishable and m.conf == cc.CONF_PH


def test_footprint_heights_only_publishable(monkeypatch):
    lay = layer([(sq(0, 0, 10), 2, -1, NPH), (sq(50, 0, 10), 3, -1, PH),
                 (sq(100, 0, 30), 30, -1, NPH)])
    monkeypatch.setattr(cc, "fetch_layer", lambda ds: lay)
    polys = {"low": sq(0, 0, 10), "ph": sq(50, 0, 10), "tower": sq(100, 0, 30)}
    heights, stats = cc.footprint_heights(polys, DS.bbox, ds=DS)
    assert set(heights) == {"low"}
    assert stats["matched"] == 3 and stats["publishable"] == 1 and stats["ph"] == 1
    assert stats["matches"]["tower"].defers


# --------------------------------------------------------------------------- remote zip + cache

class _Resp:
    def __init__(self, data, start, total):
        self.content, self.status_code = data, 206
        self.headers = {"Content-Range": f"bytes {start}-{start + len(data) - 1}/{total}"}

    def raise_for_status(self):
        pass


class FakeRemote:
    """Answers HTTP range requests from in-memory zip bytes, counting them."""

    def __init__(self, blob: bytes):
        self.blob, self.calls = blob, 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        a, b = headers["Range"].split("=")[1].split("-")
        a, b = int(a), min(int(b), len(self.blob) - 1)
        return _Resp(self.blob[a:b + 1], a, len(self.blob))


def _shapefile_zip(tmp_path) -> bytes:
    import geopandas as gpd

    con = gpd.GeoDataFrame(
        {"total_piso": [2, 3, 0, 77, 1],
         "codigo": [NPH, NPH, NPH, NPH, PH]},
        geometry=[sq(0, 0, 10), sq(0, 0, 10), sq(20, 0, 10), sq(40, 0, 10), sq(500, 0, 10)],
        crs=4326).to_crs(9377)
    bar = gpd.GeoDataFrame({"nombre": ["Centro"]}, geometry=[sq(-5, -5, 70)], crs=4326).to_crs(9377)
    d = tmp_path / "shp"
    d.mkdir()
    con.to_file(d / "Construccion.shp")
    bar.to_file(d / "Barrio.shp")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("GDB_Test.gdb/a00000001.gdbtable", b"x" * 100)
        for f in sorted(d.iterdir()):
            z.write(f, f"SHP_Test/{f.name}")
    return buf.getvalue()


@pytest.fixture
def remote(tmp_path, monkeypatch):
    import geo2stl.cache as cache_mod

    monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path / "cache")
    fake = FakeRemote(_shapefile_zip(tmp_path))
    monkeypatch.setitem(cc._URLS, "test", "https://example.invalid/blob")
    monkeypatch.setitem(cc._SESSION, "test", fake)
    monkeypatch.setattr(cc, "_LAYERS", {})
    return fake


def test_zip_directory_and_member(remote):
    d = cc.zip_directory(remote, "u")
    assert "SHP_Test/Construccion.dbf" in d and "GDB_Test.gdb/a00000001.gdbtable" in d
    assert cc.read_member(remote, "u", d["GDB_Test.gdb/a00000001.gdbtable"]) == b"x" * 100


def test_fetch_layer_cleans_reprojects_and_caches(remote):
    lay = cc.fetch_layer(DS)
    assert len(lay) == 4 and lay.n_duplicates == 1
    assert sorted(np.nan_to_num(lay.floors).tolist()) == [0.0, 0.0, 1.0, 3.0]
    c = lay.geometry[0].centroid
    assert abs(c.y - LAT) < 0.001 and abs(c.x - LON) < 0.001       # back in WGS84
    assert lay.zones == ["CENTRO"]
    assert [lay.zone_name(i) for i in range(4)].count("CENTRO") == 3
    n = remote.calls
    cc._LAYERS.clear()
    again = cc.fetch_layer(DS)                                      # from the disk cache
    assert remote.calls == n
    assert len(again) == 4 and again.zones == ["CENTRO"]
    assert again.codigo[0].decode() in (NPH, PH)
    assert again.geometry[0].equals_exact(lay.geometry[0], 1e-6)
