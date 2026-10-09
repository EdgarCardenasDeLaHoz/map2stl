"""Google Open Buildings 2.5D Temporal reader (``providers/google_ob25d.py``), offline: a fixture
manifest and a synthetic saved window stand in for the bucket."""

import json

import numpy as np
import pytest
from pyproj import Transformer
from shapely.geometry import box
from shapely.ops import transform

from city2stl.height.providers import google_ob25d as g

# The real Cartagena tile (manifest 8f_EPSG_32618_2023_06_30, folder 8ef64).
X0, Y0, CRS = 433364.0, 1158568.0, "EPSG:32618"
OBJ = "v1/geotiffs/8ef64_2023_06_30/tile_AJEU8LcTBR8.tif"


def manifest():
    return {
        "name": "projects/x/assets/open-buildings-temporal/8f_EPSG_32618_2023_06_30",
        "uriPrefix": "gs://open-buildings-temporal-data/v1/geotiffs/8ef",
        "tilesets": [{"id": "a0", "crs": CRS, "dataType": "FLOAT", "sources": [
            {"uris": ["64_2023_06_30/tile_AJEU8LcTBR8.tif"],
             "affineTransform": {"scaleX": 0.5, "translateX": X0, "scaleY": -0.5, "translateY": Y0},
             "dimensions": {"width": 25000, "height": 25000}},
            {"uris": ["64_2023_06_30/tile_far.tif"],
             "affineTransform": {"scaleX": 0.5, "translateX": X0 + 50000, "scaleY": -0.5, "translateY": Y0},
             "dimensions": {"width": 25000, "height": 25000}}]}],
        "bands": [{"id": b, "tilesetId": "a0", "tilesetBandIndex": i} for i, b in enumerate(g.BANDS)],
    }


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CITY2STL_OB25D_DIR", str(tmp_path))
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests" / "8f_EPSG_32618_2023_06_30.json").write_text(json.dumps(manifest()))
    return tmp_path


# Synthetic window: 200 x 200 px (100 m) at col 10000, row 12000 of the tile. A 20 m building
# (px 50-89) of height 6 m, presence 0.9; background presence 0.1, height 0; one no-data stripe.
C0, R0, W, H = 10000, 12000, 200, 200


def write_window(root):
    import rasterio
    from rasterio.transform import Affine

    cnt = np.full((H, W), 0.0, np.float32)
    hgt = np.zeros((H, W), np.float32)
    pre = np.full((H, W), 0.1, np.float32)
    hgt[50:90, 50:90] = 6.0
    hgt[50:90, 80:90] = 9.0          # a taller quarter: p90 picks it up
    pre[50:90, 50:90] = 0.9
    cnt[50:90, 50:90] = 0.001
    hgt[0:5, :] = g.NODATA            # no-data rows away from the building
    tile = g.Tile(OBJ, CRS, X0, Y0, 25000, 25000, "8ef64")
    d = root / "2023"
    d.mkdir(parents=True, exist_ok=True)
    p = d / g.window_name(tile, C0, R0, W, H)
    tf = Affine(0.5, 0, X0 + C0 * 0.5, 0, -0.5, Y0 - R0 * 0.5)
    with rasterio.open(p, "w", driver="GTiff", width=W, height=H, count=3, dtype="float32",
                       crs=CRS, transform=tf, nodata=g.NODATA) as ds:
        ds.write(np.stack([cnt, hgt, pre]))
    return tile, p


def utm_box_to_ll(x0, y0, x1, y1):
    inv = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform
    return transform(inv, box(x0, y0, x1, y1))


def px_box(c0, r0, c1, r1):
    """UTM box of tile pixels [c0, c1) x [r0, r1) (window coordinates)."""
    return (X0 + (C0 + c0) * 0.5, Y0 - (R0 + r1) * 0.5, X0 + (C0 + c1) * 0.5, Y0 - (R0 + r0) * 0.5)


def test_s2_tokens_match_the_bucket_folders():
    # folders and manifests read from the bucket on 2026-10-09
    assert (g.cell_token(10.40, -75.55, 7), g.cell_token(10.40, -75.55, 2)) == ("8ef64", "8f")
    assert (g.cell_token(18.45, -66.06, 7), g.cell_token(18.45, -66.06, 2)) == ("8c034", "8d")
    assert (g.cell_token(6.24, -75.58, 7), g.cell_token(6.24, -75.58, 2)) == ("8e444", "8f")
    assert g.cell_token(18.34, -64.93, 7) == "8c054"
    assert g.cell_token(18.205, -67.145, 7) == "8c02c"


def test_utm_epsg():
    assert g.utm_epsg(10.4, -75.55) == 32618
    assert g.utm_epsg(18.45, -66.06) == 32619
    assert g.utm_epsg(-12.0, -77.0) == 32718


def test_band_order_is_the_manifests():
    assert g.BANDS == ("building_fractional_count", "building_height", "building_presence")
    assert (g.B_COUNT, g.B_HEIGHT, g.B_PRESENCE) == (1, 2, 3)


def test_manifest_tiles():
    ts = g.manifest_tiles(manifest())
    assert ts[0].object == OBJ and ts[0].cell == "8ef64" and ts[0].tile_id == "AJEU8LcTBR8"
    assert ts[0].bounds() == (X0, Y0 - 12500.0, X0 + 12500.0, Y0)
    assert ts[0].url.startswith("https://storage.googleapis.com/open-buildings-temporal-data/v1/")


def test_tiles_for_bbox_offline(store):
    cartagena = (10.4295, 10.3845, -75.5221, -75.5679)
    ts = g.tiles_for_bbox(cartagena, fetch=False)
    assert [t.object for t in ts] == [OBJ]
    assert g.covers(cartagena, fetch=False)


def test_not_covered_without_a_manifest(store):
    assert not g.covers((25.8178, 25.7222, -80.1269, -80.2331), fetch=False)   # Miami
    assert not g.covers((21.295, 21.264, -157.815, -157.848), fetch=False)     # Honolulu


def test_pixel_window_clips_to_the_tile():
    t = g.Tile(OBJ, CRS, X0, Y0, 25000, 25000, "8ef64")
    assert g.pixel_window(t, X0 + 10, Y0 - 20, X0 + 20, Y0 - 10) == (20, 20, 20, 20)
    assert g.pixel_window(t, X0 - 100, Y0 - 1, X0 + 1, Y0 + 100) == (0, 0, 2, 2)
    assert g.pixel_window(t, X0 - 100, Y0 - 1, X0 - 50, Y0 + 100) is None


def test_reading_from_pixels():
    h = np.array([[5, 5, 9, 9], [5, 5, 9, 9]], np.float32)
    p = np.array([[0.9, 0.9, 0.9, 0.2], [0.9, 0.9, 0.9, g.NODATA]], np.float32)
    inside = np.ones_like(h, bool)
    r = g.reading_from_pixels(h, p, inside, tau=0.5, min_pixels=1, min_share=0.25)
    assert r.valid and r.n_px == 6 and r.share == pytest.approx(0.75)
    assert r.p50 == 5.0 and r.p90 == 9.0
    # too few pixels, or too small a share -> invalid, no heights
    r = g.reading_from_pixels(h, p, inside, tau=0.5, min_pixels=7, min_share=0.25)
    assert not r.valid and r.p50 is None and r.n_px == 6
    r = g.reading_from_pixels(h, p, inside, tau=0.95, min_pixels=1, min_share=0.25)
    assert not r.valid and r.n_px == 0
    assert g.reading_from_pixels(h, p, np.zeros_like(inside), tau=0.5) is None


def test_reading_ignores_nodata_height():
    h = np.array([[g.NODATA, 4.0]], np.float32)
    p = np.array([[0.9, 0.9]], np.float32)
    r = g.reading_from_pixels(h, p, np.ones_like(h, bool), tau=0.5, min_pixels=1, min_share=0.1)
    assert r.valid and r.n_px == 1 and r.p50 == 4.0


def test_footprint_heights_from_a_saved_window(store):
    tile, _ = write_window(store)
    bldg = utm_box_to_ll(*px_box(50, 50, 90, 90))
    lot = utm_box_to_ll(*px_box(120, 120, 180, 180))          # background only: presence 0.1
    outside = utm_box_to_ll(X0 + 9000, Y0 - 9000, X0 + 9020, Y0 - 8980)   # no saved window
    out = g.footprint_heights({"b": bldg, "lot": lot, "far": outside}, tau=0.5, fetch=False,
                              tiles=[tile])
    r = out["b"]
    assert r.valid and r.p50 == pytest.approx(6.0) and r.p90 == pytest.approx(9.0)
    assert r.share > 0.95 and r.n_px > 1500 and r.tile == "8ef64/AJEU8LcTBR8"
    assert r.count == pytest.approx(0.001 * r.n_px, rel=0.05)
    assert not out["lot"].valid and out["lot"].n_px == 0
    assert "far" not in out


def test_footprint_heights_reads_nothing_without_windows(store):
    tile = g.Tile(OBJ, CRS, X0, Y0, 25000, 25000, "8ef64")
    poly = utm_box_to_ll(*px_box(50, 50, 90, 90))
    assert g.footprint_heights({"b": poly}, fetch=False, tiles=[tile]) == {}


def test_pick_tile_prefers_the_folder_cell():
    a = g.Tile("v1/geotiffs/8ef64_2023_06_30/tile_a.tif", CRS, X0, Y0, 25000, 25000, "8ef64")
    b = g.Tile("v1/geotiffs/8ef6c_2023_06_30/tile_b.tif", CRS, X0, Y0, 25000, 25000, "8ef6c")
    lat, lon = 10.40, -75.55
    xy = {CRS: Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(lon, lat)}
    assert g._pick_tile([b, a], lat, lon, xy) is a
    assert g._pick_tile([b], lat, lon, xy) is b           # fallback: any tile containing it


def test_provider_grid_from_a_saved_window(store):
    write_window(store)
    ll = utm_box_to_ll(*px_box(0, 0, 200, 200))
    w, s, e, n = ll.bounds
    res = g.GoogleOB25DProvider(fetch=False).fetch_heights((n, s, e, w), (10, 10))
    assert res.source_name == "google_ob25d" and res.raster.shape == (10, 10)
    vals = res.raster[np.isfinite(res.raster)]
    assert vals.size and 5.0 <= vals.max() <= 9.0         # only the building passes presence
    assert np.all(res.confidence[np.isnan(res.raster)] == 0)


def test_window_names_round_trip(store):
    tile, p = write_window(store)
    assert g.local_windows(tile) == [(p, (C0, R0, W, H))]
