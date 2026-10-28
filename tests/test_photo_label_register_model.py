"""tools/photo_label/register_model.py: edge alignment of a photo against an untextured model."""

import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
import trimesh

from city2stl.skyline.photo_view import PinholeCamera

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "photo_label"))
import register_model as rm  # noqa: E402

LON0, LAT0 = -75.55, 10.42
BOXES = [  # (cx, cy, sx, sy, height)
    (0, 60, 20, 14, 18), (-35, 80, 14, 22, 26), (30, 95, 24, 18, 12), (-10, 130, 30, 16, 32),
    (55, 70, 12, 12, 22), (-60, 55, 16, 12, 10), (15, 170, 40, 20, 40), (-45, 150, 18, 18, 20),
]


def city_mesh():
    parts = [trimesh.creation.box((2400, 2400, 1)), ]
    parts[0].apply_translation((0, 100, -0.5))
    for cx, cy, sx, sy, h in BOXES:
        b = trimesh.creation.box((sx, sy, h))
        b.apply_translation((cx, cy, h / 2))
        parts.append(b)
    return trimesh.util.concatenate(parts)


def fake_photo(scene, cam, pos):
    """The model shaded by face normal under a blue sky: edges are real intensity steps."""
    h, w = cam.height, cam.width
    r, u, f = rm.basis(cam.heading_deg, cam.pitch_deg, cam.roll_deg)
    xs, ys = np.meshgrid(np.arange(w), np.arange(h))
    dz = f[2] + ((xs - w / 2) / cam.f_px) * r[2] - ((ys - h / 2) / cam.f_px) * u[2]
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (90, 150, 235)
    img[dz < 0] = (70, 70, 70)                      # the z = 0 ground plane
    tris = scene.tris[12:]
    c = rm.to_camera_space(tris, pos, cam)
    order = np.argsort(-c[:, :, 2].mean(axis=1))
    x, y, _ = rm.project(tris.astype(float), pos, cam)
    for i in order:
        n = np.cross(tris[i, 1] - tris[i, 0], tris[i, 2] - tris[i, 0]).astype(float)
        n /= np.linalg.norm(n) + 1e-9
        k = 4 if n[2] > 0.5 else int(np.argmax(np.abs(n[:2]) * [1, 1.01])) * 2 + int(n[np.argmax(np.abs(n[:2]))] > 0)
        shade = (205, 150, 110, 175, 190)[k] if k < 4 else 250
        shade = (205, 150, 110, 175, 190)[min(k, 4)]
        poly = np.stack([x[i], y[i]], 1)
        cv2.fillConvexPoly(img, np.rint(poly * 16).astype(np.int32), (shade, shade, shade - 10), cv2.LINE_8, 4)
    return img


@pytest.fixture(scope="module")
def setup():
    mesh = city_mesh()
    scene = rm.ModelScene.from_mesh(mesh, LON0, LAT0)
    cam = PinholeCamera(LAT0, LON0, 8.0, 5.0, 3.0, 0.0, 600.0, 640, 480)
    pos = rm.cam_position(cam, LON0, LAT0)
    return scene, cam, pos


def test_wireframe_has_corners_and_no_rim(setup):
    scene, _, _ = setup
    assert len(scene.seg) >= 8 * len(BOXES) * 0.8      # base rings are dropped
    assert not (scene.seg[:, :, 2] < 2).all(1).any()
    assert np.abs(scene.seg[:, :, 0]).max() < 1199


def test_depth_nearest_wins(setup):
    scene, cam, pos = setup
    d = rm.render_depth(scene.tris, pos, cam)
    assert np.isfinite(d).mean() > 0.3
    # the box at (0, 60) is 18 m high and ~60 m away: its wall is nearer than the ground behind it
    assert np.nanmin(np.where(np.isfinite(d), d, np.nan)) < 60


def test_refine_recovers_pose(setup):
    scene, cam, pos = setup
    photo = fake_photo(scene, cam, pos)
    start = rm.shift_camera(replace(cam, heading_deg=cam.heading_deg + 4.0, pitch_deg=cam.pitch_deg - 1.0,
                                    f_px=cam.f_px), 4.0, -3.0, 0.0)
    fit = rm.refine(photo, scene, start)
    dh = (fit.cam.heading_deg - cam.heading_deg + 180) % 360 - 180
    p_fit = rm.cam_position(fit.cam, LON0, LAT0)
    assert abs(dh) < 0.5
    assert np.linalg.norm(p_fit - pos) < 2.0
    assert fit.score > 0.45
