"""Render the city model from a Street View pano's camera and set it beside the photo (F-PANOVIEW).

    python tools/eval/render_pano_view.py [--npz cache/city_models/<key>.npz] [--out runs/panoview/granada]
        [--tag after]

Reads ``manifest.json`` (``tools/eval/pano_manifest.py``) and writes ``view_<n>.png`` per view:
left the panorama with the render's skyline in red, right the render with the same skyline.

The model cache holds model millimetres; they are turned back into metres with the true print
scale (``Georef``: horizontal mm per metre from the export bbox, vertical the same, i.e. no
exaggeration, checked against the buildings' ``height_m``), so the camera sits at the pano's
lat/lon, 2.5 m above the terrain, with the pano's heading, pitch and field of view. A pano whose
position falls inside a building footprint (``--polygons``; OSM outlines and Google positions
disagree by a few metres) is moved to the nearest point outside it, plus ``CLEAR_M``.

No GL is installed, so this is a small software rasteriser: faces are clipped to the near plane,
sorted far to near and filled with ``cv2.fillConvexPoly`` (painter's algorithm) at 2x and
averaged down. Shading is a lambert term by face normal plus distance haze.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from tools.eval.score_export_heights import (  # noqa: E402
    GRANADA_BBOX,
    Georef,
    load_model_meshes,
    load_polygons,
)

EYE_M = 2.5
CLEAR_M = 1.5   # how far outside a footprint a moved camera stands
MAX_DIST_M = 3500.0
NEAR_M = 0.5
SS = 2   # supersampling
SKY = np.array([235, 215, 190], np.float32)   # BGR
HAZE = np.array([225, 215, 205], np.float32)
PART_COLORS = {   # BGR
    "terrain": (120, 140, 150), "buildings": (190, 205, 225), "fortifications": (150, 175, 205),
    "walls": (150, 175, 205), "towers": (150, 175, 205), "roads": (95, 95, 100), "railways": (80, 80, 85),
    "green": (100, 150, 110), "trees": (70, 130, 70), "rivers": (170, 140, 90),
}
DEFAULT_NPZ = REPO / "cache" / "city_models" / "2ee6aaa3538a0aa6a183872e7739a6f56deb5f0c.npz"
DEFAULT_POLYGONS = "db1dfbff27f1"   # the buildings of that export


def camera_basis(heading_deg: float, pitch_deg: float):
    """(forward, right, up) unit vectors in east/north/up metres."""
    h, p = np.radians(heading_deg), np.radians(pitch_deg)
    fwd = np.array([np.sin(h) * np.cos(p), np.cos(h) * np.cos(p), np.sin(p)])
    right = np.array([np.cos(h), -np.sin(h), 0.0])
    return fwd, right, np.cross(right, fwd)


class Scene:
    """Model parts in metres (east, north, up) plus the terrain height lookup."""

    def __init__(self, npz: Path, geo: Georef, trees: bool = False, polygons=None):
        skip = {"__merged__"} | (set() if trees else {"trees"})   # canopy solids swallow street-level cameras
        parts = {n: m for n, m in load_model_meshes(npz).items() if n not in skip}
        self.sx, self.sy, self.sz = geo.wmm / geo.width_m, geo.hmm / geo.height_m, geo.z_mm_per_m
        scale = np.array([self.sx, self.sy, self.sz])
        verts, faces, color, off = [], [], [], 0
        for name, (v, f) in parts.items():
            verts.append(v / scale)
            faces.append(f.astype(np.int64) + off)
            color.append(np.tile(np.array(PART_COLORS.get(name, (160, 160, 160)), np.float32), (len(f), 1)))
            off += len(v)
        self.v = np.vstack(verts)
        self.f = np.vstack(faces)
        self.color = np.vstack(color)
        from scipy.interpolate import LinearNDInterpolator
        tv = parts["terrain"][0] / scale
        self._terrain = LinearNDInterpolator(tv[:, :2], tv[:, 2])
        self._tv = tv
        self.polys = None   # footprints in metres, for moving a camera out of a building
        if polygons is not None and len(polygons):
            import shapely
            self.polys = shapely.transform(np.asarray(polygons, dtype=object),
                                           lambda c: c / [self.sx, self.sy])
            self._ptree = shapely.STRtree(self.polys)

    def outside(self, e: float, n: float) -> tuple[float, float, bool]:
        """(e, n) unchanged, or moved just outside the footprint it falls in; the flag says which."""
        if self.polys is None:
            return e, n, False
        import shapely
        from shapely.ops import nearest_points
        pt, moved, block = shapely.Point(e, n), False, None
        for _ in range(4):   # the step out can land in a neighbour; take that one in too
            hit = self._ptree.query(pt, predicate="within")
            if not len(hit):
                break
            block = shapely.union_all(np.concatenate([self.polys[hit], [block]]) if block is not None else self.polys[hit])
            pt, moved = nearest_points(block.buffer(CLEAR_M).boundary, pt)[0], True
        return float(pt.x), float(pt.y), moved

    def ground(self, e: float, n: float) -> float:
        z = float(self._terrain(e, n))
        if np.isnan(z):   # outside the TIN: nearest vertex
            z = float(self._tv[np.argmin(np.hypot(self._tv[:, 0] - e, self._tv[:, 1] - n)), 2])
        return z


def _clip_near(tri_c: np.ndarray, near: float) -> list[np.ndarray]:
    """Camera-space triangle clipped to z >= near: 0, 1 or 2 triangles."""
    out, poly = [], []
    for i in range(3):
        a, b = tri_c[i], tri_c[(i + 1) % 3]
        if a[2] >= near:
            poly.append(a)
        if (a[2] >= near) != (b[2] >= near):
            t = (near - a[2]) / (b[2] - a[2])
            poly.append(a + t * (b - a))
    for k in range(1, len(poly) - 1):
        out.append(np.array([poly[0], poly[k], poly[k + 1]]))
    return out


def render(scene: Scene, geo: Georef, lat: float, lon: float, heading: float, pitch: float,
           fov: float, size: int = 640):
    """(BGR image, skyline rows per column, camera info) of the model seen from the pano."""
    x_mm, y_mm = geo.to_mm(lon, lat)
    e0, n0, moved = scene.outside(float(x_mm) / scene.sx, float(y_mm) / scene.sy)
    g = scene.ground(e0, n0)
    eye = np.array([e0, n0, g + EYE_M])
    fwd, right, up = camera_basis(heading, pitch)
    rel = scene.v - eye
    cam = np.stack([rel @ right, rel @ up, rel @ fwd], 1)
    dist = np.hypot(rel[:, 0], rel[:, 1])
    f = scene.f
    zc = cam[:, 2][f]
    keep = (zc.max(1) >= NEAR_M) & (dist[f].min(1) < MAX_DIST_M)
    idx = np.flatnonzero(keep)
    W = size * SS
    focal = (W / 2) / np.tan(np.radians(fov) / 2)
    tri = cam[f[idx]]                       # (k, 3, 3) camera space
    col = scene.color[idx]
    # normals for shading, world space
    w = scene.v[f[idx]]
    nrm = np.cross(w[:, 1] - w[:, 0], w[:, 2] - w[:, 0])
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
    nrm *= np.sign(nrm[:, 2:3] + 1e-9)      # face up-ish: the TIN and walls are two-sided here
    sun = np.array([-0.4, -0.5, 0.75]) / np.linalg.norm([-0.4, -0.5, 0.75])
    shade = 0.45 + 0.55 * np.clip(nrm @ sun, 0, 1)
    depth = tri[:, :, 2].mean(1)
    # triangles crossing the near plane are clipped one at a time (few)
    partial = np.flatnonzero(tri[:, :, 2].min(1) < NEAR_M)
    extra_tri, extra_col, extra_shade, extra_depth = [], [], [], []
    for i in partial:
        for t in _clip_near(tri[i], NEAR_M):
            extra_tri.append(t)
            extra_col.append(col[i])
            extra_shade.append(shade[i])
            extra_depth.append(t[:, 2].mean())
    full = np.setdiff1d(np.arange(len(idx)), partial)
    tri = np.concatenate([tri[full], np.array(extra_tri).reshape(-1, 3, 3)])
    col = np.concatenate([col[full], np.array(extra_col).reshape(-1, 3)])
    shade = np.concatenate([shade[full], np.array(extra_shade)])
    depth = np.concatenate([depth[full], np.array(extra_depth)])
    u = W / 2 + focal * tri[:, :, 0] / tri[:, :, 2]
    v = W / 2 - focal * tri[:, :, 1] / tri[:, :, 2]
    on = ~((u.max(1) < 0) | (u.min(1) > W) | (v.max(1) < 0) | (v.min(1) > W))
    u, v, col, shade, depth = u[on], v[on], col[on], shade[on], depth[on]
    order = np.argsort(-depth)
    haze = np.clip(depth[order] / MAX_DIST_M, 0, 1)[:, None] ** 0.7 * 0.55
    rgb = (col[order] * shade[order, None]) * (1 - haze) + HAZE * haze
    pts = np.clip(np.stack([u[order], v[order]], 2), -1e5, 1e5).astype(np.int32)
    img = np.empty((W, W, 3), np.uint8)
    img[:] = SKY.astype(np.uint8)
    mask = np.zeros((W, W), np.uint8)
    for p, c in zip(pts, rgb.astype(np.uint8).tolist()):
        cv2.fillConvexPoly(img, p, c)
        cv2.fillConvexPoly(mask, p, 255)
    img = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    cover = cv2.resize(mask, (size, size), interpolation=cv2.INTER_AREA) > 127
    sky_row = np.where(cover.any(0), cover.argmax(0), size)
    return img, sky_row, {"ground_m": round(g, 1), "eye_xy_m": [round(e0), round(n0)], "moved": moved, "tris": int(len(pts))}


def draw_skyline(img: np.ndarray, rows: np.ndarray, color=(0, 0, 255)) -> np.ndarray:
    out = img.copy()
    xs = np.arange(len(rows))
    ok = rows < len(rows)
    pts = np.stack([xs[ok], rows[ok]], 1).astype(np.int32).reshape(-1, 1, 2)
    if len(pts) > 1:
        cv2.polylines(out, [pts], False, color, 2, cv2.LINE_AA)
    return out


def compose(pano: np.ndarray, rend: np.ndarray, rows: np.ndarray, title: str) -> np.ndarray:
    size = rend.shape[0]
    pano = cv2.resize(pano, (size, size))
    both = np.hstack([draw_skyline(pano, rows), draw_skyline(rend, rows)])
    bar = np.full((34, both.shape[1], 3), 255, np.uint8)
    cv2.putText(bar, title, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (30, 30, 30), 1, cv2.LINE_AA)
    return np.vstack([bar, both])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--npz", type=Path, default=DEFAULT_NPZ)
    ap.add_argument("--polygons", default=DEFAULT_POLYGONS,
                    help="city_polygons file or key prefix of the export's buildings; '' = none")
    ap.add_argument("--out", type=Path, default=REPO / "runs" / "panoview" / "granada")
    ap.add_argument("--bbox", type=float, nargs=4, default=GRANADA_BBOX, metavar=("S", "W", "N", "E"))
    ap.add_argument("--trees", action="store_true", help="render the canopy solids too")
    ap.add_argument("--tag", default="", help="suffix for the output names, e.g. 'after'")
    ap.add_argument("--heading-offset", type=float, default=0.0, help="degrees added to the pano heading")
    a = ap.parse_args()
    geo = Georef(tuple(a.bbox))
    scene = Scene(a.npz, geo, a.trees, load_polygons(a.polygons)[0] if a.polygons else None)
    man = json.loads((a.out / "manifest.json").read_text(encoding="utf-8"))
    for n, v in enumerate(man["views"], 1):
        pano_file = a.out / v["file"]
        if not pano_file.is_file():
            continue
        pano = cv2.imread(str(pano_file))
        rend, rows, info = render(scene, geo, v["lat"], v["lon"], v["heading"] + a.heading_offset,
                                  v["pitch"], v["fov"], size=pano.shape[0])
        title = f"{n}. {v['name']}  pano {v['date']}  heading {v['heading']:.0f}  ground {info['ground_m']} m" + \
            ("  (moved out of a footprint)" if info["moved"] else "")
        name = f"view_{n}{'_' + a.tag if a.tag else ''}.png"
        cv2.imwrite(str(a.out / name), compose(pano, rend, rows, title))
        print(name, info)


if __name__ == "__main__":
    from city2stl.resources import scratch_guard
    scratch_guard(max_workers=2)
    main()
