"""city2stl.registration.align_edit: the helpers and endpoints that tests/test_plate_align_router.py
does not reach (raster loading, geometry, load errors, refetch, refine guardrails, score gap).

Packs live in tmp_path (ALIGN_DATA_DIR / ALIGN_GROUND_TRUTH_DIR); the network is faked.
"""

from __future__ import annotations

import base64
import json
import math

import cv2
import numpy as np
import pytest

from city2stl.registration import align_edit as ae

SLUG = "edit_xx"
IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]


def _blocks(n=96, dy=0, dx=0):
    a = np.full((n, n), np.nan)
    for y0, x0 in ((10, 10), (10, 55), (55, 10), (55, 55)):
        a[y0 + dy:y0 + dy + 28, x0 + dx:x0 + dx + 28] = 12.0
    return a


def _write_pack(data, slug=SLUG, stl=None, osm=None, **meta_over):
    folder = data / slug
    folder.mkdir(parents=True, exist_ok=True)
    stl = _blocks() if stl is None else stl
    osm = _blocks() if osm is None else osm
    np.save(folder / "stl_heightmap.npy", np.nan_to_num(stl, nan=0.0))
    np.save(folder / "osm_buildings.npy", osm)
    meta = {"slug": slug, "resolution": 96, "cell_size_m": 5.0, "stl_shape": [96, 96],
            "osm_shape": [96, 96], "osm_bbox_nsew": [40.01, 40.0, -75.0, -75.01]}
    meta.update(meta_over)
    (folder / "meta.json").write_text(json.dumps(meta))
    return folder


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, gt = tmp_path / "data", tmp_path / "gt"
    data.mkdir()
    monkeypatch.setenv("ALIGN_DATA_DIR", str(data))
    monkeypatch.setenv("ALIGN_GROUND_TRUTH_DIR", str(gt))
    return {"data": data, "gt": gt}


# ------------------------------------------------------------------------------ rasters

def test_png_data_uri_roundtrips_pixels():
    img = np.arange(64, dtype=np.uint8).reshape(8, 8) * 3
    uri = ae._png_data_uri(img)
    assert uri.startswith("data:image/png;base64,")
    raw = np.frombuffer(base64.b64decode(uri.split(",", 1)[1]), np.uint8)
    np.testing.assert_array_equal(cv2.imdecode(raw, cv2.IMREAD_UNCHANGED), img)


def test_norm_u8_scales_to_the_99th_percentile_and_cleans_nan():
    hm = np.zeros((10, 10))
    hm[:5, :5] = 20.0
    hm[0, 0] = np.nan
    hm[9, 9] = -4.0                       # negatives clip to 0
    out = ae._norm_u8(hm)
    assert out.dtype == np.uint8 and out.shape == hm.shape
    assert out[0, 0] == 0 and out[9, 9] == 0
    assert out[2, 2] == 255               # the plateau is the 99th percentile
    assert out[8, 8] == 0
    # a spike above the percentile saturates instead of compressing the rest
    hm2 = np.full((20, 20), 10.0)
    hm2[0, 0] = 1000.0
    out2 = ae._norm_u8(hm2)
    assert out2[5, 5] == 255 and out2[0, 0] == 255


def test_norm_u8_all_empty_is_zeros():
    assert ae._norm_u8(np.zeros((4, 4))).max() == 0
    assert ae._norm_u8(np.full((4, 4), np.nan)).max() == 0


def test_load_gray_prefers_npy_and_keeps_nan(env):
    folder = _write_pack(env["data"])
    cv2.imwrite(str(folder / "osm_buildings.png"), np.full((96, 96), 77, np.uint8))
    arr = ae._load_gray(SLUG, "osm_buildings")
    assert arr.dtype == np.float64 and arr.shape == (96, 96)
    assert np.isnan(arr[0, 0]) and arr[20, 20] == 12.0      # npy, with its NaNs, not the png


def test_load_gray_falls_back_to_the_png(env):
    folder = env["data"] / SLUG
    folder.mkdir()
    cv2.imwrite(str(folder / "thing.png"), np.full((6, 7), 77, np.uint8))
    arr = ae._load_gray(SLUG, "thing")
    assert arr.dtype == np.float64 and arr.shape == (6, 7) and (arr == 77).all()


def test_load_gray_errors(env):
    folder = env["data"] / SLUG
    folder.mkdir()
    with pytest.raises(FileNotFoundError, match="missing raster"):
        ae._load_gray(SLUG, "nothing")
    (folder / "junk.png").write_bytes(b"not a png")
    with pytest.raises(RuntimeError, match="could not decode"):
        ae._load_gray(SLUG, "junk")


def test_meta_reads_json_and_rejects_unknown_slug(env):
    _write_pack(env["data"], city="Edit")
    assert ae._meta(SLUG)["city"] == "Edit"
    with pytest.raises(FileNotFoundError, match="unknown city slug: ghost"):
        ae._meta("ghost")


# ------------------------------------------------------------------------------ geometry

def test_apply_affine():
    M = [[2.0, 0.0, 5.0], [0.0, -1.0, 3.0]]
    assert ae._apply(M, 4, 7) == (13.0, -4.0)
    assert ae._apply(IDENTITY, 1.5, 2.5) == (1.5, 2.5)


def test_invert2x3_is_a_true_inverse():
    M = np.array([[0.9, -0.3, 12.0], [0.3, 0.9, -7.0]])
    inv = ae._invert2x3(M)
    assert inv.shape == (2, 3)
    for x, y in ((0, 0), (30, 12), (-5, 8)):
        u, v = ae._apply(M, x, y)
        np.testing.assert_allclose(ae._apply(inv, u, v), (x, y), atol=1e-9)
    np.testing.assert_allclose(ae._invert2x3(inv), M, atol=1e-9)


def test_stl_footprint_bbox_identity_covers_the_osm_bbox():
    bbox = (40.01, 40.0, -75.0, -75.01)
    n, s, e, w = ae._stl_footprint_bbox(IDENTITY, bbox, (50, 50), (100, 100))
    # identity: the STL frame is the top-left 50x50 of the 100x100 OSM raster (row 0 = north)
    assert (n, e) == pytest.approx((40.01, -75.005))
    assert (s, w) == pytest.approx((40.005, -75.01))


def test_stl_footprint_bbox_follows_scale_and_shift():
    bbox = (41.0, 40.0, -74.0, -75.0)
    M = [[2.0, 0.0, 10.0], [0.0, 2.0, 20.0]]
    n, s, e, w = ae._stl_footprint_bbox(M, bbox, (10, 10), (100, 100))
    assert w == pytest.approx(-75.0 + 0.10)
    assert e == pytest.approx(-75.0 + 0.30)
    assert n == pytest.approx(41.0 - 0.20)
    assert s == pytest.approx(41.0 - 0.40)


def test_stl_footprint_bbox_of_a_rotation_is_the_hull():
    th = math.radians(45)
    M = [[math.cos(th), -math.sin(th), 50.0], [math.sin(th), math.cos(th), 0.0]]
    n, s, e, w = ae._stl_footprint_bbox(M, (1.0, 0.0, 1.0, 0.0), (10, 10), (100, 100))
    # a 10 px square turned 45 degrees spans 10*sqrt(2) px in each axis
    assert (e - w) == pytest.approx(10 * math.sqrt(2) / 100, rel=1e-6)
    assert (n - s) == pytest.approx(10 * math.sqrt(2) / 100, rel=1e-6)


def test_square_bbox_is_square_in_metres_and_scaled_by_margin():
    box, side = ae._square_bbox(40.01, 40.0, -75.0, -75.02, margin=1.0)
    n, s, e, w = box
    lat_c = 40.005
    m_lon = ae.M_PER_DEG_LON_EQ * math.cos(math.radians(lat_c))
    assert (n - s) * ae.M_PER_DEG_LAT == pytest.approx(side)
    assert (e - w) * m_lon == pytest.approx(side)
    # the wider side (0.02 deg of longitude, ~1.7 km) sets the side length
    assert side == pytest.approx(0.02 * m_lon)
    assert (n + s) / 2 == pytest.approx(lat_c) and (e + w) / 2 == pytest.approx(-75.01)
    _, side2 = ae._square_bbox(40.01, 40.0, -75.0, -75.02, margin=1.5)
    assert side2 == pytest.approx(1.5 * side)


def test_square_bbox_center_moves_the_window_but_keeps_its_size():
    plain, side = ae._square_bbox(40.01, 40.0, -75.0, -75.01, margin=1.2)
    moved, side_c = ae._square_bbox(40.01, 40.0, -75.0, -75.01, margin=1.2, center=(41.0, -70.0))
    assert side_c == pytest.approx(side, rel=0.02)           # cos(lat) differs slightly
    assert (moved[0] + moved[1]) / 2 == pytest.approx(41.0)
    assert (moved[2] + moved[3]) / 2 == pytest.approx(-70.0)
    assert (plain[0] + plain[1]) / 2 == pytest.approx(40.005)


def test_cell_size_m_is_the_mean_side_over_resolution():
    # 0.01 deg of latitude is ~1113 m; the longitude side at lat 40 is ~852 m
    c = ae._cell_size_m(40.01, 40.0, -75.0, -75.01, 100)
    assert 8.5 < c < 10.0
    assert ae._cell_size_m(40.01, 40.0, -75.0, -75.01, 200) == pytest.approx(c / 2)


# ------------------------------------------------------------------------------ save / load

def test_save_without_payload_key_writes_the_whole_body(env):
    r = ae.api_save({"slug": SLUG, "transform": IDENTITY})
    assert r["ok"] and r["bytes"] > 0
    saved = json.loads((env["gt"] / f"{SLUG}.json").read_text())
    assert saved["transform"] == IDENTITY and saved["slug"] == SLUG


def test_save_with_payload_key_writes_only_the_payload(env):
    ae.api_save({"slug": SLUG, "payload": {"transform": IDENTITY}})
    assert json.loads((env["gt"] / f"{SLUG}.json").read_text()) == {"transform": IDENTITY}


def test_load_missing_and_unreadable(env):
    r = ae.api_load({"slug": SLUG})
    assert r["found"] is False and "error" not in r
    env["gt"].mkdir()
    (env["gt"] / f"{SLUG}.json").write_text("{ not json")
    r = ae.api_load({"slug": SLUG})
    assert r["ok"] and r["found"] is False and r["error"].startswith("unreadable")


# ------------------------------------------------------------------------------ refetch

def _jpeg_b64(shape, colour=(10, 200, 30)):
    img = np.full((*shape, 3), colour, np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _decode_uri(uri):
    raw = np.frombuffer(base64.b64decode(uri.split(",", 1)[1]), np.uint8)
    return cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)


@pytest.fixture
def fakes(monkeypatch):
    """Fake the OSM raster and the satellite fetch; record what was asked."""
    import city2stl.osm_raster as osm_raster
    import geo2stl.sat2stl as sat2stl

    seen = {"calls": []}

    def hm(bbox, resolution=512, **kw):
        seen["calls"].append(("hm", tuple(bbox), resolution))
        arr = np.full((resolution, resolution), np.nan)
        arr[: resolution // 4, : resolution // 4] = 9.0
        return {"heightmap": arr}

    def masks(bbox, resolution=512, **kw):
        seen["calls"].append(("masks", tuple(bbox), resolution))
        water = np.zeros((resolution, resolution), bool)
        water[-4:, :] = True
        return {"water": water}

    def sat(north, south, east, west, dim=1024, **kw):
        seen["calls"].append(("sat", (north, south, east, west), dim))
        return _jpeg_b64((dim, dim))

    monkeypatch.setattr(osm_raster, "get_osm_building_heightmap", hm)
    monkeypatch.setattr(osm_raster, "get_osm_semantic_masks", masks)
    monkeypatch.setattr(sat2stl, "fetch_satellite_tiles", sat)
    return seen


def test_refetch_returns_the_new_window_and_three_images(env, fakes):
    _write_pack(env["data"])
    r = ae.api_refetch({"slug": SLUG, "matrix": IDENTITY, "resolution": 64, "margin": 1.5})
    assert r["ok"] and r["resolution"] == 64 and r["margin"] == 1.5
    assert r["osm_shape"] == [64, 64]
    N, S, E, W = r["bbox_nsew"]
    assert N > S and E > W
    # the window is a metre square centred on the footprint hull, 1.5x its size
    hull = ae._stl_footprint_bbox(IDENTITY, [40.01, 40.0, -75.0, -75.01], [96, 96], [96, 96])
    (exp, side) = ae._square_bbox(*hull, margin=1.5)
    assert r["bbox_nsew"] == pytest.approx(list(exp))
    assert r["side_m"] == pytest.approx(side)
    assert r["center"] == pytest.approx([(N + S) / 2, (E + W) / 2])
    assert r["cell_size_m"] == pytest.approx(ae._cell_size_m(N, S, E, W, 64))
    assert r["fit_scale"] == pytest.approx(1.5 * 96 / 64)
    assert r["buildings_px"] == 16 * 16                      # the faked 16x16 corner
    osm_img = _decode_uri(r["images"]["osm_buildings"])
    assert osm_img.shape == (64, 64) and osm_img.dtype == np.uint8
    assert osm_img[0, 0] == 255 and osm_img[40, 40] == 0
    water = _decode_uri(r["images"]["osm_water"])
    assert water[-1, 0] == 255 and water[0, 0] == 0
    sat = _decode_uri(r["images"]["sat"])
    assert sat.shape == (64, 64, 3)
    # satellite arrives row 0 = north and is flipped to row 0 = south; a flat colour survives it
    assert tuple(int(v) for v in sat[5, 5]) == pytest.approx((10, 200, 30), abs=6)   # BGR, as encoded
    kinds = [c[0] for c in fakes["calls"]]
    assert kinds == ["hm", "masks", "sat"]
    assert fakes["calls"][2][2] == 64


def test_refetch_center_override_moves_the_window(env, fakes):
    _write_pack(env["data"])
    r = ae.api_refetch({"slug": SLUG, "matrix": IDENTITY, "resolution": 32,
                        "center": [41.5, -70.25]})
    assert r["center"] == pytest.approx([41.5, -70.25])
    default = ae.api_refetch({"slug": SLUG, "matrix": IDENTITY, "resolution": 32})
    assert default["margin"] == 1.5 and default["side_m"] == pytest.approx(r["side_m"], rel=0.02)


def test_refetch_body_overrides_meta_and_unknown_slug_fails(env, fakes):
    _write_pack(env["data"], resolution=48)
    r = ae.api_refetch({"slug": SLUG, "matrix": IDENTITY})
    assert r["resolution"] == 48                             # from meta
    r = ae.api_refetch({"slug": SLUG, "matrix": IDENTITY, "resolution": 24,
                        "bbox_nsew": [41.0, 40.0, -74.0, -75.0],
                        "stl_shape": [10, 10], "osm_shape": [20, 20]})
    assert r["resolution"] == 24
    # 10 of 20 px of a 1 degree window: half a degree each way, then 1.5x
    hull_h = 0.5 * ae.M_PER_DEG_LAT
    assert r["side_m"] == pytest.approx(1.5 * hull_h, rel=0.02)
    with pytest.raises(FileNotFoundError):
        ae.api_refetch({"slug": "ghost", "matrix": IDENTITY})


def test_refetch_survives_failing_water_and_satellite(env, monkeypatch, fakes):
    import city2stl.osm_raster as osm_raster
    import geo2stl.sat2stl as sat2stl

    def broken(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(osm_raster, "get_osm_semantic_masks", broken)
    monkeypatch.setattr(sat2stl, "fetch_satellite_tiles", broken)
    _write_pack(env["data"])
    r = ae.api_refetch({"slug": SLUG, "matrix": IDENTITY, "resolution": 32})
    assert r["ok"]
    assert not _decode_uri(r["images"]["osm_water"]).any()
    assert not _decode_uri(r["images"]["sat"]).any()
    assert _decode_uri(r["images"]["sat"]).shape == (32, 32, 3)
    assert _decode_uri(r["images"]["osm_buildings"]).any()


def test_refetch_building_failure_propagates(env, monkeypatch, fakes):
    import city2stl.osm_raster as osm_raster

    def broken(*a, **k):
        raise RuntimeError("overpass down")
    monkeypatch.setattr(osm_raster, "get_osm_building_heightmap", broken)
    _write_pack(env["data"])
    with pytest.raises(RuntimeError, match="overpass down"):
        ae.api_refetch({"slug": SLUG, "matrix": IDENTITY, "resolution": 32})


def test_fetch_sat_resizes_and_flips_vertically(monkeypatch):
    import geo2stl.sat2stl as sat2stl

    img = np.zeros((40, 40, 3), np.uint8)
    img[:20] = (255, 255, 255)                   # north half white (row 0 = north)
    ok, buf = cv2.imencode(".png", img)
    asked = {}

    def fake(n, s, e, w, dim=1024, **kw):
        asked["dim"] = dim
        return base64.b64encode(buf.tobytes()).decode("ascii")

    monkeypatch.setattr(sat2stl, "fetch_satellite_tiles", fake)
    out = ae._fetch_sat((41.0, 40.0, -74.0, -75.0), (20, 30))
    assert out.shape == (20, 30, 3) and out.dtype == np.uint8
    assert asked["dim"] == 30
    assert out.flags["C_CONTIGUOUS"]
    # flipped: the white north half is now at the end (row 0 = south is dark)
    assert out[2].max() == 0 and out[-3].min() == 255


def test_fetch_sat_bad_image_raises(monkeypatch):
    import geo2stl.sat2stl as sat2stl
    monkeypatch.setattr(sat2stl, "fetch_satellite_tiles",
                        lambda *a, **k: base64.b64encode(b"nope").decode())
    with pytest.raises(RuntimeError, match="decode failed"):
        ae._fetch_sat((1.0, 0.0, 1.0, 0.0), (8, 8))


# ------------------------------------------------------------------------------ refine

def _city(n=128, dy=0, dx=0):
    a = np.full((n, n), np.nan)
    rects = [(8, 10, 30, 22), (12, 50, 18, 40), (45, 8, 26, 30), (50, 60, 36, 20),
             (95, 15, 24, 34), (92, 70, 30, 28), (30, 95, 20, 25), (5, 100, 22, 15)]
    for y, x, h, w in rects:
        a[y + dy:y + dy + h, x + dx:x + dx + w] = 12.0
    return a


def test_refine_pulls_a_shifted_plate_towards_the_map(env):
    # The plate content sits 5 px south-east of the map's, so the right answer is (-5, -5).
    # Refine is a snap, not an exact solver: it must improve the overlap, move the right way and
    # stay inside the guardrails.
    _write_pack(env["data"], stl=_city(dy=5, dx=5), osm=_city(), stl_shape=[128, 128],
                osm_shape=[128, 128], resolution=128)
    r = ae.api_refine({"slug": SLUG, "matrix": IDENTITY})
    assert r["ok"] and r["motion"] == "affine" and r["signal"] == "building_edges"
    assert r["improved"] is True and r["converged"] is True
    assert r["iou_after"] > r["iou_before"] + 0.3
    assert r["tx"] < -1.0 and r["ty"] < -1.0
    assert math.hypot(r["tx"] + 5.0, r["ty"] + 5.0) < 0.7 * math.hypot(5.0, 5.0)
    assert abs(r["scale"] - 1.0) < ae._MAX_SCALE_RATIO - 1.0
    assert abs(r["rot_deg"]) < ae._MAX_ROT_DEG
    assert r["delta_translation_px"] == pytest.approx(math.hypot(r["tx"], r["ty"]))
    assert r["delta_rot_deg"] == pytest.approx(r["rot_deg"])
    assert 0.0 < r["confidence"] <= 1.0
    assert any("kept" in t for t in r["trace"])
    assert len(r["trace"]) == len(ae._BLUR_LADDER)
    assert r["matrix"] != IDENTITY and len(r["matrix"]) == 2 and len(r["matrix"][0]) == 3


def test_refine_with_an_already_perfect_seed_keeps_it(env):
    _write_pack(env["data"])
    r = ae.api_refine({"slug": SLUG, "matrix": IDENTITY})
    assert r["improved"] is False and r["converged"] is False
    assert r["matrix"] == IDENTITY
    assert r["iou_after"] == r["iou_before"] and r["iou_before"] > 0.9
    assert math.isnan(r["confidence"])
    assert r["delta_translation_px"] == 0.0 and r["delta_rot_deg"] == 0.0


def test_refine_rejects_a_bad_matrix_and_unknown_slug(env):
    _write_pack(env["data"])
    with pytest.raises(ValueError, match="2x3"):
        ae.api_refine({"slug": SLUG, "matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]})
    with pytest.raises(FileNotFoundError):
        ae.api_refine({"slug": "ghost", "matrix": IDENTITY})


def test_refine_guardrail_rejects_a_runaway_result(env, monkeypatch):
    _write_pack(env["data"], stl=_blocks(dy=5, dx=5), osm=_blocks())
    runaway = np.array([[1.0, 0.0, 200.0], [0.0, 1.0, 200.0]])
    monkeypatch.setattr(ae, "_ecc_once", lambda t, s, seed, iters=500: (0.9, runaway))
    r = ae.api_refine({"slug": SLUG, "matrix": IDENTITY})
    assert r["improved"] is False and r["matrix"] == IDENTITY
    assert len(r["trace"]) == 3 and all("rejected, drifted" in t for t in r["trace"])


def test_refine_guardrail_rejects_scale_and_rotation(env, monkeypatch):
    _write_pack(env["data"])
    big = np.array([[1.5, 0.0, 0.0], [0.0, 1.5, 0.0]])
    monkeypatch.setattr(ae, "_ecc_once", lambda t, s, seed, iters=500: (0.9, big))
    r = ae.api_refine({"slug": SLUG, "matrix": IDENTITY})
    assert all("scale" in t and "rejected" in t for t in r["trace"])
    th = math.radians(35)
    turned = np.array([[math.cos(th), -math.sin(th), 0.0], [math.sin(th), math.cos(th), 0.0]])
    monkeypatch.setattr(ae, "_ecc_once", lambda t, s, seed, iters=500: (0.9, turned))
    r = ae.api_refine({"slug": SLUG, "matrix": IDENTITY})
    assert all("rotated 35deg" in t for t in r["trace"])
    assert r["matrix"] == IDENTITY


def test_refine_survives_an_ecc_failure(env, monkeypatch):
    _write_pack(env["data"])

    def fail(*a, **k):
        raise cv2.error("Bad: error: ECC did not converge in function findTransformECC")
    monkeypatch.setattr(ae, "_ecc_once", fail)
    r = ae.api_refine({"slug": SLUG, "matrix": IDENTITY})
    assert r["improved"] is False and r["matrix"] == IDENTITY
    assert len(r["trace"]) == 3 and all("failed" in t for t in r["trace"])


def test_refine_ignores_a_gain_below_the_noise_floor(env, monkeypatch):
    _write_pack(env["data"], stl=_blocks(dy=1, dx=0), osm=_blocks())
    nudge = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, -0.05]])
    monkeypatch.setattr(ae, "_ecc_once", lambda t, s, seed, iters=500: (0.8, nudge))
    monkeypatch.setattr(ae, "_MIN_IOU_GAIN_ABS", 0.9)         # nothing can clear this floor
    r = ae.api_refine({"slug": SLUG, "matrix": IDENTITY})
    assert r["improved"] is False and r["matrix"] == IDENTITY


def test_ecc_once_returns_a_forward_matrix_and_a_correlation():
    img = np.zeros((80, 80), np.float32)
    img[20:40, 20:40] = 1.0
    img[50:70, 45:65] = 1.0
    t = cv2.GaussianBlur(img, (15, 15), 4.0)
    moved = np.roll(np.roll(img, 3, axis=0), 4, axis=1)       # the source is 3 down, 4 right
    s = cv2.GaussianBlur(moved, (15, 15), 4.0)
    cc, M = ae._ecc_once(t, s, np.array(IDENTITY))
    assert cc > 0.9 and M.shape == (2, 3)
    # forward = source px -> target px, so it undoes the shift
    assert (M[0, 2], M[1, 2]) == pytest.approx((-4.0, -3.0), abs=0.7)


# ------------------------------------------------------------------------------ score

def test_score_gap_is_in_metres_and_grows_with_the_offset(env):
    _write_pack(env["data"], cell_size_m=2.0)
    good = ae.api_score({"slug": SLUG, "matrix": IDENTITY})
    off = ae.api_score({"slug": SLUG, "matrix": [[1, 0, 6], [0, 1, 0]]})
    assert good["ok"] and good["cell_size_m"] == 2.0
    # the plate and map edge detectors differ by a couple of pixels even when aligned
    assert good["iou"] > 0.9 and good["edge_gap_m"] < 2.5 * 2.0
    assert off["iou"] < good["iou"]
    # a 6 px shift, 2 m cells: edges sit at most 6 px (12 m) from the nearest map edge
    assert good["edge_gap_m"] < off["edge_gap_m"] <= 12.0 + 1e-6


def test_score_gap_is_none_when_the_plate_leaves_the_map(env):
    _write_pack(env["data"])
    r = ae.api_score({"slug": SLUG, "matrix": [[1, 0, 500], [0, 1, 500]]})
    assert r["edge_gap_m"] is None and r["iou"] == 0.0


def test_score_defaults_the_cell_size_to_one_metre(env):
    _write_pack(env["data"], cell_size_m=None)
    assert ae.api_score({"slug": SLUG, "matrix": IDENTITY})["cell_size_m"] == 1.0


def test_score_rejects_a_bad_matrix(env):
    _write_pack(env["data"])
    with pytest.raises(ValueError, match="2x3"):
        ae.api_score({"slug": SLUG, "matrix": [1, 0, 0]})
