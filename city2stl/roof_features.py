"""One feature vector per building, from a footprint ring and its satellite crop.

The production classifier reads three numbers off the image -- gradient
strength, gradient anisotropy, brightness ridge -- and decides with hand-set
thresholds on them. Three numbers is not enough to separate a flat concrete
roof from a pyramid, which is how Cartagena came back 99.6 per cent pyramidal.

This is the same idea with enough evidence to be worth training on. Three
groups:

  geometry   what the footprint alone says. A 40 m × 12 m block and a 7 m × 7 m
             house have different roofs regardless of what the image shows.
  appearance brightness, colour and gradient structure inside the footprint,
             measured in the footprint's own frame rather than the image's, so
             a ridge running north-south and one running east-west look alike.
  context    surroundings and the tags a building carries anyway.

Everything is computed from data available at inference time: no plate, no
height raster, no roof tag.
"""
import math

import numpy as np

from geo2stl.geo import M_PER_DEG_LAT, m_per_deg_lon

FEATURES = [
    # geometry
    "area_m2", "log_area", "perimeter_m", "compactness", "elongation",
    "rectangularity", "n_vertices", "minor_m", "major_m",
    # tags that exist without any roof survey
    "levels", "height_m", "is_house", "is_apartments", "is_residential",
    "is_commercial", "is_industrial", "is_church", "is_generic",
    # appearance
    "bright_mean", "bright_std", "bright_cv", "sat_mean", "sat_std",
    "red_green", "blue_green", "grad_strength", "grad_anisotropy",
    "brightness_ridge", "edge_density", "grad_axis_align",
    "contrast_surround", "border_interior_ratio",
    # profiles in the footprint's own frame: 6 bins along, 6 across
    "along_0", "along_1", "along_2", "along_3", "along_4", "along_5",
    "across_0", "across_1", "across_2", "across_3", "across_4", "across_5",
    "along_peak_offset", "across_peak_offset", "along_symmetry",
    "across_symmetry",
    # shadow
    "shadow_ratio", "shadow_elongation", "shadow_tri", "shadow_conf",
    # where on earth, as a climate proxy rather than a city label
    "abs_lat",
]

HOUSE = ("house", "detached", "semidetached_house", "terrace", "bungalow",
         "static_caravan", "cabin", "hut")
APARTMENTS = ("apartments", "residential_block", "dormitory")
RESIDENTIAL = ("residential", "hotel", "farm", "houseboat")
COMMERCIAL = ("commercial", "retail", "office", "supermarket", "kiosk")
INDUSTRIAL = ("industrial", "warehouse", "hangar", "factory", "shed",
              "service", "garage", "garages", "carport", "roof")
CHURCH = ("church", "chapel", "cathedral", "mosque", "synagogue", "temple",
          "religious")


def _num(tags, key):
    """A tag read as a number, or NaN. OSM writes '12 m' and '3;4' too."""
    v = tags.get(key)
    if v is None:
        return float("nan")
    try:
        return float(str(v).split(";")[0].strip().split(" ")[0])
    except ValueError:
        return float("nan")


def _ring_metres(ring):
    """The ring as metres from its own centroid, x east and y north."""
    lons = np.array([p[0] for p in ring], dtype=np.float64)
    lats = np.array([p[1] for p in ring], dtype=np.float64)
    if len(lons) > 1 and lons[0] == lons[-1] and lats[0] == lats[-1]:
        lons, lats = lons[:-1], lats[:-1]
    clat, clon = lats.mean(), lons.mean()
    mx = m_per_deg_lon(clat)
    return np.column_stack([(lons - clon) * mx, (lats - clat) * M_PER_DEG_LAT])


def _geometry(ring):
    p = _ring_metres(ring)
    if len(p) < 3:
        return None
    x, y = p[:, 0], p[:, 1]
    area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) -
                           np.dot(y, np.roll(x, -1))))
    seg = np.sqrt(np.sum((np.roll(p, -1, axis=0) - p) ** 2, axis=1))
    perim = float(seg.sum())
    if area < 4.0 or perim < 4.0:
        return None

    # Principal axes, so the crop can be measured in the building's frame.
    cov = np.cov((p - p.mean(axis=0)).T)
    evals, evecs = np.linalg.eigh(cov)
    order = np.argsort(evals)[::-1]
    evecs = evecs[:, order]
    proj = (p - p.mean(axis=0)) @ evecs
    major = float(proj[:, 0].max() - proj[:, 0].min())
    minor = float(proj[:, 1].max() - proj[:, 1].min())

    return {
        "area_m2": area,
        "log_area": math.log(area),
        "perimeter_m": perim,
        "compactness": 4 * math.pi * area / (perim ** 2),
        "elongation": minor / major if major > 0 else 1.0,
        "rectangularity": area / (major * minor) if major * minor > 0 else 0.0,
        "n_vertices": float(len(p)),
        "minor_m": minor,
        "major_m": major,
        "_axis": evecs[:, 0],       # long axis, in (east, north) metres
        "_centroid": p.mean(axis=0),
    }


def _tag_features(tags):
    b = str(tags.get("building", "yes")).lower()
    return {
        "levels": _num(tags, "building:levels"),
        "height_m": _num(tags, "height"),
        "is_house": float(b in HOUSE),
        "is_apartments": float(b in APARTMENTS),
        "is_residential": float(b in RESIDENTIAL),
        "is_commercial": float(b in COMMERCIAL),
        "is_industrial": float(b in INDUSTRIAL),
        "is_church": float(b in CHURCH),
        "is_generic": float(b in ("yes", "building", "")),
    }


def _mask_for_ring(ring, north, south, east, west, h, w):
    import cv2
    lons = np.array([p[0] for p in ring], dtype=np.float64)
    lats = np.array([p[1] for p in ring], dtype=np.float64)
    col = (lons - west) / max(east - west, 1e-12) * w
    row = (north - lats) / max(north - south, 1e-12) * h
    pts = np.column_stack([col, row]).astype(np.int32)
    m = np.zeros((h, w), np.uint8)
    cv2.fillPoly(m, [pts], 1)
    return m.astype(bool)


def _sobel(gray):
    kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], np.float32)
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            sl = np.roll(np.roll(gray, dy, 0), dx, 1)
            gx += kx[dy + 1, dx + 1] * sl
            gy += kx[dx + 1, dy + 1] * sl
    return gx[1:-1, 1:-1], gy[1:-1, 1:-1]


def _bins(values, coord, n=6):
    """Mean of `values` in `n` equal bins of `coord`, normalised to its mean."""
    out = np.zeros(n, np.float64)
    if coord.size == 0:
        return out
    lo, hi = float(coord.min()), float(coord.max())
    if hi - lo < 1e-9:
        return out
    idx = np.clip(((coord - lo) / (hi - lo) * n).astype(int), 0, n - 1)
    for i in range(n):
        sel = idx == i
        out[i] = float(values[sel].mean()) if sel.any() else np.nan
    m = np.nanmean(out)
    if not np.isfinite(m) or abs(m) < 1e-9:
        return np.zeros(n, np.float64)
    out = np.where(np.isfinite(out), out, m)
    return out / m


def _appearance(rgb, mask, axis_en, pixel_m):
    """Everything read off the image, measured in the building's own frame."""
    f = {}
    h, w = mask.shape
    rgbf = rgb.astype(np.float32)
    gray = rgbf.mean(axis=2)
    inside = mask
    if inside.sum() < 12:
        return None
    outside = (~mask) & _dilate(mask, 3)

    g = gray[inside]
    f["bright_mean"] = float(g.mean())
    f["bright_std"] = float(g.std())
    f["bright_cv"] = float(g.std() / max(g.mean(), 1.0))
    mx = rgbf.max(axis=2)[inside]
    mn = rgbf.min(axis=2)[inside]
    sat = (mx - mn) / np.maximum(mx, 1.0)
    f["sat_mean"] = float(sat.mean())
    f["sat_std"] = float(sat.std())
    green = np.maximum(rgbf[:, :, 1][inside], 1.0)
    f["red_green"] = float((rgbf[:, :, 0][inside] / green).mean())
    f["blue_green"] = float((rgbf[:, :, 2][inside] / green).mean())
    f["contrast_surround"] = (float(g.mean() / max(gray[outside].mean(), 1.0))
                              if outside.any() else 1.0)

    gx, gy = _sobel(gray)
    inner = inside[1:-1, 1:-1]
    if inner.sum() < 12:
        return None
    mag = np.sqrt(gx ** 2 + gy ** 2)
    mi = mag[inner]
    f["grad_strength"] = float(mi.mean() / max(g.mean(), 1.0))
    f["edge_density"] = float((mi > mi.mean() * 1.5).mean())

    ang = np.arctan2(gy[inner], gx[inner])
    strong = mi > mi.mean() * 0.5
    if strong.sum() > 8:
        hist, _ = np.histogram(ang[strong], bins=8, range=(-np.pi, np.pi))
        p = hist.astype(float) / max(hist.sum(), 1)
        ent = float(-np.sum(p * np.log(p + 1e-9)))
        f["grad_anisotropy"] = float(1.0 - ent / math.log(8))
        # A pitched roof's brightness gradient runs across the ridge, so the
        # dominant gradient direction sits near the footprint's short axis.
        # Measured against the axis, this survives the building's orientation.
        dom = float(np.arctan2(
            np.sin(2 * ang[strong]).mean(), np.cos(2 * ang[strong]).mean()) / 2)
        axis_ang = math.atan2(-axis_en[1], axis_en[0])   # north is -row
        f["grad_axis_align"] = float(abs(math.cos(2 * (dom - axis_ang))))
    else:
        f["grad_anisotropy"] = 0.0
        f["grad_axis_align"] = 0.0

    rows, cols = np.nonzero(inside)
    cy, cx = rows.mean(), cols.mean()
    ax_c, ax_r = float(axis_en[0]), float(-axis_en[1])       # image frame
    along = (cols - cx) * ax_c + (rows - cy) * ax_r
    across = -(cols - cx) * ax_r + (rows - cy) * ax_c
    vals = gray[inside]
    pa = _bins(vals, along)
    pc = _bins(vals, across)
    for i in range(6):
        f[f"along_{i:d}"] = float(pa[i])
        f[f"across_{i:d}"] = float(pc[i])
    f["along_peak_offset"] = float(abs(int(np.argmax(pa)) - 2.5) / 2.5)
    f["across_peak_offset"] = float(abs(int(np.argmax(pc)) - 2.5) / 2.5)
    f["along_symmetry"] = float(1.0 - np.abs(pa - pa[::-1]).mean())
    f["across_symmetry"] = float(1.0 - np.abs(pc - pc[::-1]).mean())
    f["brightness_ridge"] = float(np.clip(pc.std(), 0.0, 2.0))

    border = inside & ~_erode(inside, 2)
    interior = _erode(inside, 2)
    f["border_interior_ratio"] = (
        float(gray[border].mean() / max(gray[interior].mean(), 1.0))
        if border.any() and interior.any() else 1.0)

    f.update(_shadow(gray, inside, pixel_m))
    return f


def _dilate(mask, k):
    import cv2
    return cv2.dilate(mask.astype(np.uint8),
                      np.ones((2 * k + 1, 2 * k + 1), np.uint8)).astype(bool)


def _erode(mask, k):
    import cv2
    return cv2.erode(mask.astype(np.uint8),
                     np.ones((2 * k + 1, 2 * k + 1), np.uint8)).astype(bool)


def _shadow(gray, inside, pixel_m):
    """How much dark ground sits beside the building, and what shape it is.

    Not a height estimate -- the sun angle is unknown here -- but a tall block
    with a long hard shadow and a low house with almost none are different
    buildings, and the roof follows.
    """
    import cv2
    near = _dilate(inside, 12) & ~inside
    if near.sum() < 20 or not inside.any():
        return {"shadow_ratio": 0.0, "shadow_elongation": 0.0,
                "shadow_tri": 0.0, "shadow_conf": 0.0}
    thr = float(np.percentile(gray[near], 20))
    dark = near & (gray <= min(thr, float(gray[inside].mean()) * 0.6))
    ratio = float(dark.sum()) / float(inside.sum())
    if dark.sum() < 12:
        return {"shadow_ratio": ratio, "shadow_elongation": 0.0,
                "shadow_tri": 0.0, "shadow_conf": 0.0}
    ys, xs = np.nonzero(dark)
    pts = np.column_stack([xs, ys]).astype(np.float32)
    (_, _), (mw, mh), _ = cv2.minAreaRect(pts)
    major, minor = max(mw, mh), max(min(mw, mh), 1e-3)
    hull = cv2.convexHull(pts)
    hull_area = float(cv2.contourArea(hull))
    return {
        "shadow_ratio": ratio,
        "shadow_elongation": float(major / minor),
        "shadow_tri": float(1.0 - dark.sum() / max(hull_area, 1.0)),
        "shadow_conf": float(min(1.0, dark.sum() / 60.0)),
    }


def extract(ring, tags, crop):
    """A dict of FEATURES for one building, or None if it cannot be measured.

    `crop` is (rgb, north, south, east, west, m_per_px) from roof_tiles.
    """
    geom = _geometry(ring)
    if geom is None:
        return None
    rgb, north, south, east, west, pixel_m = crop
    mask = _mask_for_ring(ring, north, south, east, west,
                          rgb.shape[0], rgb.shape[1])
    app = _appearance(rgb, mask, geom["_axis"], pixel_m)
    if app is None:
        return None

    out = {k: v for k, v in geom.items() if not k.startswith("_")}
    out.update(_tag_features(tags))
    out.update(app)
    out["abs_lat"] = abs(0.5 * (north + south))
    return out


def to_row(feat):
    """FEATURES order, NaN kept -- the tree model handles missing tags."""
    return [float(feat.get(k, float("nan"))) for k in FEATURES]
