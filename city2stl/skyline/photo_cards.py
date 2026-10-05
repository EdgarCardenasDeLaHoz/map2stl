"""Per-photo images for the benchmark page, in the style of the seed pages (F-WEB2, 2026-10-05).

For every kept photo:

- the photo with each measured OSM tower numbered left to right: a solid line at the roof the
  photo's outline gives, a dashed line at the roof its OSM height predicts (from the photo's
  fitted tilt and camera height), brackets at the tower's columns;
- a location map: OSM footprints around the camera, the camera and its view cone, and the
  same towers filled and numbered.

The numbers are the rows of the card's table, so the page shows which building is which.

    bg = background_polygons(osm["buildings"]["features"], towers)
    out = render_photo_card(image_rgb, prof, pose, h_cam, towers, rows, bg, out_dir, key)
    # out = {"overlay": "assets/photos/<key>_osm.jpg", "map": "assets/photos/<key>_map.png",
    #        "labels": {tower index: number}}
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from . import photo_heights as ph

#: Width of the saved photo overlay, px (the page shows it at most ~730 px wide).
OVERLAY_WIDTH = 1400

_TAB10 = ((31, 119, 180), (255, 127, 14), (44, 160, 44), (214, 39, 40), (148, 103, 189),
          (140, 86, 75), (227, 119, 194), (127, 127, 127), (188, 189, 34), (23, 190, 207))


#: Colour of a tower whose reading is not used (``row["flag"]``).
FLAG_GREY = (150, 150, 150)


def label_color(n: int, row: dict | None = None) -> tuple[int, int, int]:
    """RGB colour of label ``n`` (1-based), shared by the photo and the map; grey for a reading
    that is not used."""
    return FLAG_GREY if row is not None and row.get("flag") else _TAB10[(n - 1) % len(_TAB10)]


def background_polygons(features: list[dict], towers) -> tuple[list[np.ndarray], np.ndarray]:
    """Every OSM building ring in the tower table's metre frame, and their centroids."""
    kx = 111_320.0 * math.cos(math.radians(towers.lat0))
    polys = []
    for f in features:
        g = f.get("geometry") or {}
        if g.get("type") == "Polygon":
            rings = [g["coordinates"][0]]
        elif g.get("type") == "MultiPolygon":
            rings = [p[0] for p in g["coordinates"]]
        else:
            continue
        for ring in rings:
            a = np.asarray(ring, float)[:, :2]
            polys.append(np.column_stack([(a[:, 0] - towers.lon0) * kx,
                                          (a[:, 1] - towers.lat0) * 111_320.0]))
    cent = np.array([p.mean(axis=0) for p in polys]) if polys else np.zeros((0, 2))
    return polys, cent


def label_order(rows: list[dict], measures: dict) -> list[dict]:
    """The card's tower rows that were measured in this photo, left to right."""
    rows = [r for r in rows if r["tower"] in measures]
    return sorted(rows, key=lambda r: (measures[r["tower"]].x0 + measures[r["tower"]].x1) / 2)


def draw_photo_overlay(image_rgb: np.ndarray, prof, pose, ordered: list[dict], measures: dict,
                       tilt_deg: float, h_cam: float) -> np.ndarray:
    """The photo with the outline (white) and each tower's roof lines and number."""
    import cv2  # noqa: PLC0415

    over = image_rgb.copy()
    h, w = over.shape[:2]
    sx, sy = w / prof.width, h / prof.height
    f = ph._focal(prof, pose)
    pts = np.array([[i * sx, y * sy] for i, y in enumerate(prof.y_top) if np.isfinite(y)], np.int32)
    if len(pts) > 1:
        cv2.polylines(over, [pts.reshape(-1, 1, 2)], False, (255, 255, 255), max(1, w // 900),
                      cv2.LINE_AA)
    lw, fs, tick = max(2, w // 500), max(0.6, w / 1600), int(h * 0.06)
    placed: list[tuple[int, int, int, int]] = []
    for n, row in enumerate(ordered, 1):
        m, col = measures[row["tower"]], label_color(n, row)
        x0, x1 = int(m.x0 * sx), int(m.x1 * sx)
        y_roof = int(m.roof_row * sy)
        e_osm = math.degrees(math.atan((m.osm_height_m - h_cam) / m.dist_m)) - tilt_deg
        y_osm = int((prof.height / 2.0 - f * math.tan(math.radians(e_osm))) * sy)
        for xx in (x0, x1):
            cv2.line(over, (xx, y_roof), (xx, y_roof + tick), col, max(1, lw - 1), cv2.LINE_AA)
        cv2.line(over, (x0, y_roof), (x1, y_roof), col, lw, cv2.LINE_AA)
        for xx in range(x0, x1, max(6, lw * 4)):                     # OSM roof, dashed
            cv2.line(over, (xx, y_osm), (min(xx + lw * 2, x1), y_osm), col, max(1, lw - 1),
                     cv2.LINE_AA)
        txt = str(n)
        (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, fs, 2)
        cx = (x0 + x1) // 2
        ty = max(th + 6, min(y_roof, y_osm) - 10)
        # stack a label upward while it would cover an earlier one
        while ty > th + 6 and any(not (cx + tw // 2 + 6 < a or cx - tw // 2 - 6 > c
                                       or ty + 6 < b or ty - th - 6 > d) for a, b, c, d in placed):
            ty -= th + 10
        placed.append((cx - tw // 2 - 4, ty - th - 5, cx + tw // 2 + 4, ty + 5))
        cv2.line(over, (cx, ty + 5), (cx, min(y_roof, y_osm)), col, 1, cv2.LINE_AA)
        cv2.rectangle(over, (cx - tw // 2 - 4, ty - th - 5), (cx + tw // 2 + 4, ty + 5), col, -1)
        cv2.putText(over, txt, (cx - tw // 2, ty), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 2,
                    cv2.LINE_AA)
    return over


def draw_location_map(path: Path, towers, pose, ordered: list[dict], bg) -> None:
    """Camera, view cone and the numbered towers over the OSM footprints around them."""
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415
    from matplotlib.collections import PolyCollection  # noqa: PLC0415
    from matplotlib.patches import Polygon as MplPolygon  # noqa: PLC0415

    polys, cent = bg
    cam = np.array(towers.to_xy(pose.lat, pose.lon), float)
    verts = [np.asarray(towers.verts[r["tower"]], float) for r in ordered]
    allp = np.vstack([cam[None, :], *verts]) if verts else cam[None, :]
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    span = float(max(hi - lo)) * 1.12 + 200.0
    mid = (lo + hi) / 2.0
    fig, ax = plt.subplots(figsize=(6.2, 6.2), dpi=110)
    if len(cent):
        near = np.hypot(*(cent - mid).T) < span
        ax.add_collection(PolyCollection([p for p, k in zip(polys, near, strict=True) if k],
                                         facecolor="#e3e3e3", edgecolor="#c9c9c9", lw=0.3))
    reach = span * 1.5
    hd, half = math.radians(pose.heading_deg), math.radians(pose.hfov_deg / 2.0)
    cone = [cam] + [cam + reach * np.array([math.sin(hd + a), math.cos(hd + a)])
                    for a in np.linspace(-half, half, 24)]
    ax.add_patch(MplPolygon(cone, closed=True, facecolor="#1f77b4", alpha=0.08,
                            edgecolor="#1f77b4", lw=0.8))
    sep = span * 0.06                        # minimum spacing between labels, metres
    spots: list[np.ndarray] = []
    for n, (v, row) in enumerate(zip(verts, ordered, strict=True), 1):
        col = np.array(label_color(n, row)) / 255.0
        ax.add_patch(MplPolygon(v, closed=True, facecolor=col, edgecolor="k", lw=0.6, alpha=0.95))
        c = v.mean(axis=0)
        pos = c
        for k in range(1, 40):               # spiral out until clear of earlier labels
            if all(np.hypot(*(pos - q)) >= sep for q in spots):
                break
            a = k * 2.4
            pos = c + sep * (0.6 + 0.25 * k) * np.array([math.cos(a), math.sin(a)])
        spots.append(pos)
        if np.hypot(*(pos - c)) > 1:
            ax.plot([c[0], pos[0]], [c[1], pos[1]], color=col, lw=0.8)
        ax.text(pos[0], pos[1], str(n), fontsize=11, ha="center", va="center", color="white",
                weight="bold", bbox=dict(boxstyle="circle,pad=0.2", fc=col, ec="k", lw=0.4))
    ax.plot(*cam, marker="^", color="#1f77b4", ms=10, mec="k")
    ax.set_xlim(mid[0] - span / 2, mid[0] + span / 2)
    ax.set_ylim(mid[1] - span / 2, mid[1] + span / 2)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    bar = 500 if span > 1500 else 200
    xb, yb = mid[0] - span * 0.45, mid[1] - span * 0.46
    ax.plot([xb, xb + bar], [yb, yb], color="k", lw=2)
    ax.text(xb + bar / 2, yb + span * 0.015, f"{bar} m", ha="center", fontsize=8)
    ax.text(mid[0] + span * 0.46, mid[1] + span * 0.44, "N ↑", ha="right", fontsize=10,
            weight="bold")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def render_photo_card(image_rgb: np.ndarray, prof, pose, h_cam: float, towers, rows: list[dict],
                      bg, out_dir: Path, key: str) -> dict:
    """Write ``<key>_osm.jpg`` and ``<key>_map.png`` into ``out_dir`` (``<report>/assets/photos``).

    ``rows`` are the card's measured towers (``{"tower": index, ...}``). Returns the two paths
    relative to the report and the label number per tower index.
    """
    import cv2  # noqa: PLC0415

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    measures = {m.index: m for m in ph.measure_towers(prof, towers, pose, h_cam=h_cam)}
    ordered = label_order(rows, measures)
    used = [measures[r["tower"]] for r in ordered]
    if len(used) >= 2:
        tilt, h_fit = ph.fit_tilt_height(used, np.array([m.osm_height_m for m in used]))
    else:
        tilt, h_fit = 0.0, h_cam
    # draw at OVERLAY_WIDTH: a small cached photo (some are 500 px) is enlarged first so the
    # lines and numbers stay sharp on the page; a large one is reduced
    s = OVERLAY_WIDTH / image_rgb.shape[1]
    image_rgb = cv2.resize(image_rgb, (OVERLAY_WIDTH, int(round(image_rgb.shape[0] * s))),
                           interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    over = draw_photo_overlay(image_rgb, prof, pose, ordered, measures, tilt, h_fit)
    cv2.imwrite(str(out_dir / f"{key}_osm.jpg"), cv2.cvtColor(over, cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    draw_location_map(out_dir / f"{key}_map.png", towers, pose, ordered, bg)
    rel = out_dir.name if out_dir.parent.name != "assets" else f"assets/{out_dir.name}"
    return {"overlay": f"{rel}/{key}_osm.jpg", "map": f"{rel}/{key}_map.png",
            "labels": {r["tower"]: n for n, r in enumerate(ordered, 1)}}
