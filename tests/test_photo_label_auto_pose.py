"""tools/photo_label/auto_pose.py: skyline from a photo, DEM horizon, start-pose search."""
from __future__ import annotations

import importlib.util
import math
import types
from pathlib import Path

import numpy as np
import pytest

from city2stl.skyline.photo_view import PinholeCamera, dirs_of

MODULE = Path(__file__).resolve().parents[1] / "tools" / "photo_label" / "auto_pose.py"
spec = importlib.util.spec_from_file_location("photo_label_auto_pose", MODULE)
ap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ap)

LAT0, LON0, CELL = 37.0, -3.5, 0.0003
N = 400
KX = ap.M_PER_DEG_LAT * math.cos(math.radians(LAT0 + N * CELL / 2))


def _synthetic_dem():
    """A mound at the centre, an east-west ridge 2.3 km north with a bump, a hill to the east."""
    t = types.SimpleNamespace(c=LON0, a=CELL, f=LAT0 + N * CELL, e=-CELL)
    lat = LAT0 + N * CELL - (np.arange(N) + 0.5) * CELL
    lon = LON0 + (np.arange(N) + 0.5) * CELL
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    clat, clon = LAT0 + N * CELL / 2, LON0 + N * CELL / 2
    north = (la - clat) * ap.M_PER_DEG_LAT
    east = (lo - clon) * KX
    z = 40.0 * np.exp(-(np.hypot(north, east) / 500.0) ** 2)
    z += 250.0 * np.exp(-((north - 2300.0) / 300.0) ** 2) * (1.0 + 0.8 * np.exp(-((east + 600.0) / 500.0) ** 2))
    z += 160.0 * np.exp(-(np.hypot(north - 400.0, east - 2600.0) / 450.0) ** 2)
    r, th = np.hypot(north, east), np.arctan2(east, north)      # a ring of uneven hills round the centre
    z += 130.0 * (1.2 + np.sin(3 * th) + 0.6 * np.sin(7 * th + 1.0) + 0.4 * np.sin(13 * th))         * np.exp(-(((r - 1900.0) / 600.0) ** 2))
    return types.SimpleNamespace(z=z, t=t), clat, clon


def _render(dem, cam, width, height):
    """A photo whose sky/ground boundary is the DEM horizon seen by ``cam``, projected by the
    pinhole model (independent of auto_pose.horizon_rows)."""
    az, el, _ = ap.dem_horizon(dem, cam.lat, cam.lon, cam.elev_m)
    x, y = cam.project_dirs(dirs_of(az, el))
    ok = np.isfinite(x) & (x > -5) & (x < width + 5)
    order = np.argsort(x[ok])
    row = np.interp(np.arange(width), x[ok][order], y[ok][order])
    img = np.empty((height, width, 3), np.uint8)
    img[:] = (110, 90, 60)
    for c in range(width):
        r = int(round(row[c]))
        img[: max(0, min(height, r)), c] = (60, 120, 220)
    return img, row


# --------------------------------------------------------------------------- photo_skyline


def test_photo_skyline_blue_sky():
    h, w = 300, 400
    img = np.empty((h, w, 3), np.uint8)
    img[:] = (110, 90, 60)
    true = (120 + 0.15 * np.arange(w)).astype(int)
    for x in range(w):
        img[: true[x], x] = (60, 120, 220)
    sky = ap.photo_skyline(img)
    assert sky.shape == (w,)
    ok = np.isfinite(sky)
    assert ok.mean() > 0.95
    assert np.nanmax(np.abs(sky[ok] - true[ok])) <= 2.0


def test_photo_skyline_no_sky_is_nan():
    img = np.full((200, 300, 3), (110, 100, 90), np.uint8)
    assert np.isnan(ap.photo_skyline(img)).all()


def test_photo_skyline_ignores_ground_blue_pond():
    """Blue not connected to the top edge is not sky."""
    h, w = 200, 300
    img = np.full((h, w, 3), (110, 90, 60), np.uint8)
    img[:60] = (60, 120, 220)
    img[120:160, 100:200] = (60, 120, 220)
    sky = ap.photo_skyline(img)
    assert np.nanmax(sky) <= 61.0


# --------------------------------------------------------------------------- horizon


def test_horizon_rows_match_pinhole_projection():
    az = np.arange(0, 360, ap.AZ_STEP)
    el = 8 * np.sin(np.radians(az * 3)) + 2
    cam = PinholeCamera(0, 0, 0, 40.0, -12.0, 0.0, 900.0, 1200, 700)
    x, y = cam.project_dirs(dirs_of(az, el))
    ok = np.isfinite(x) & (x > 0) & (x < 1200)
    o = np.argsort(x[ok])
    xs = np.array([100, 400, 700, 1000])
    want = np.interp(xs, x[ok][o], y[ok][o])
    got = ap.horizon_rows(el, [40.0], [-12.0], [900.0], xs, 1200, 700)[0]
    assert np.allclose(got, want, atol=0.2)


def test_dem_horizon_sees_ridge():
    dem, clat, clon = _synthetic_dem()
    az, el, _ = ap.dem_horizon(dem, clat, clon, 42.0, canopy_m=0.0)
    north = el[np.argmin(np.abs(az - 0.0))]
    south = el[np.argmin(np.abs(az - 180.0))]
    assert north > 3.0                 # the ridge 2.3 km north
    assert north > south + 2.0         # and nothing as high to the south


# --------------------------------------------------------------------------- candidate_poses


def test_candidate_poses_recovers_known_camera():
    dem, clat, clon = _synthetic_dem()
    z0 = float(ap._dem_sampler(dem)(np.array([clat]), np.array([clon]))[0])
    cam = PinholeCamera(clat, clon, z0 + 2.0, 20.0, -6.0, 0.0, 800.0, 1000, 600)
    img, _ = _render(dem, cam, 1000, 600)
    res = ap.candidate_poses(img, dem, center_lat=clat, center_lon=clon, radius_m=900.0, top_k=3)
    assert res, "no candidates"
    best = res[0]
    assert set(best) >= {"lat", "lon", "elev", "heading", "pitch", "roll", "f", "width", "height",
                         "score", "source"}
    assert best["source"] == "grid" and best["roll"] == 0.0
    assert (best["width"], best["height"]) == (1000, 600)
    assert abs((best["heading"] - 20.0 + 180) % 360 - 180) <= 2.0
    assert abs(best["pitch"] - (-6.0)) <= 1.0
    assert ap._dist_m(best["lat"], best["lon"], clat, clon) <= 150.0
    assert best["score"] < 3.0
    assert [r["score"] for r in res] == sorted(r["score"] for r in res)


def test_candidate_poses_exif_and_viewpoint_sources():
    dem, clat, clon = _synthetic_dem()
    z0 = float(ap._dem_sampler(dem)(np.array([clat]), np.array([clon]))[0])
    cam = PinholeCamera(clat, clon, z0 + 2.0, 20.0, -6.0, 0.0, 800.0, 1000, 600)
    img, _ = _render(dem, cam, 1000, 600)
    ex = ap.candidate_poses(img, dem, center_lat=clat, center_lon=clon,
                            exif={"lat": clat, "lon": clon}, top_k=1)
    assert ex and ex[0]["source"] == "exif"
    assert abs((ex[0]["heading"] - 20.0 + 180) % 360 - 180) <= 2.0
    vp = ap.candidate_poses(img, dem, center_lat=clat, center_lon=clon, radius_m=100.0,
                            viewpoints=[(clat, clon)], top_k=1)
    assert vp and vp[0]["source"] == "viewpoint"


def test_candidate_poses_no_sky_returns_empty():
    dem, clat, clon = _synthetic_dem()
    img = np.full((300, 400, 3), (120, 110, 100), np.uint8)
    assert ap.candidate_poses(img, dem, center_lat=clat, center_lon=clon) == []


# --------------------------------------------------------------------------- viewpoints


def test_viewpoints_from_fc():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"tourism": "viewpoint"},
         "geometry": {"type": "Point", "coordinates": [-3.59, 37.17]}},
        {"type": "Feature", "properties": {"name": "Torre", "height": "41 m", "building": "tower"},
         "geometry": {"type": "Polygon", "coordinates": [[[-3.60, 37.10], [-3.60, 37.12],
                                                         [-3.58, 37.12], [-3.58, 37.10], [-3.60, 37.10]]]}},
        {"type": "Feature", "properties": {"name": "House", "height": "9"},
         "geometry": {"type": "Point", "coordinates": [-3.5, 37.0]}},
        {"type": "Feature", "properties": {"building": "yes"}, "geometry": {"type": "Point", "coordinates": [0, 0]}},
    ]}
    vp = ap.viewpoints_from_fc(fc)
    assert len(vp) == 2
    assert vp[0] == pytest.approx((37.17, -3.59))
    assert vp[1][0] == pytest.approx(37.108, abs=0.01)
    assert ap.viewpoints_from_fc({}) == []
