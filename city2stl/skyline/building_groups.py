"""Identify buildings in a skyline image by groups of buildings, not one at a time.

One tower in Bocagrande looks like the next: on Cartagena's Commons photos with known cameras,
the outline search (``skyline_match.locate``) put 0 of 17 within 300 m (2026-10-06). The
arrangement of a few neighbours -- their spacing, widths and relative heights -- is far more
distinctive than any one of them, so the matching unit here is a group of three skyline
segments.

Segments (same definition on both sides):

- image: a run of columns whose skyline (topmost non-sky pixel) belongs to one building
  instance (MobileSAM, ``building_instances``): centre, width and top, in pixels;
- model: a run of 0.1-deg bearings whose predicted skyline (``skyline_match.predicted_outline``
  with owners) is one OSM tower, seen from a candidate camera: centre, width and top, in
  degrees.

A group's descriptor is invariant to the image's unknown zoom (FOV), pan and tilt: positions,
widths and top differences divided by the group's span (pixels and degrees scale alike, square
pixels; columns are taken as linear in bearing, fine for FOVs up to ~60 deg). Every image group
is looked up among the model groups of every candidate camera (a KD-tree); each match votes for
a camera, heading and scale. The pose most image groups agree on wins, and each image segment
is identified as the model tower it falls on under that pose. Votes only propose poses; each
proposal is verified against every building in the image (:func:`verify`).

Status (2026-10-06): exact on synthetic skylines (incl. a missing and a spurious building); on
Cartagena Commons photos with known cameras it does not yet place them (0 of 16 within 1 km).
At the true camera the OSM model's segments do not line up with the photo's: most model heights
are fallbacks or unchecked readings (towers float above anything visible), and MobileSAM splits
towers differently from OSM footprints. Next: a trusted reference (tagged towers + trusted drone
readings, or the drone panos' own instance groups, whose footprints are known).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np

from . import skyline_match as sm


@dataclass(frozen=True)
class Segment:
    """One building forming the skyline: ``centre``, ``width`` and ``top`` in image pixels
    (``top`` measured upwards) or in degrees (model). ``ident``: instance id or tower index."""
    centre: float
    width: float
    top: float
    ident: int


@dataclass
class Pose:
    camera: int                      # index into the candidate cameras
    lat: float
    lon: float
    h_cam: float
    heading_deg: float               # bearing of the image centre
    px_per_deg: float
    votes: int                       # image groups that proposed this pose
    ids: dict[int, tuple[int, int]] = field(default_factory=dict)   # instance -> (tower, support)
    agree: int = 0                   # image buildings that line up with a tower under the pose


# ------------------------------------------------------------------------ segments

def image_segments(instances: np.ndarray, sky: np.ndarray, min_cols: int = 4) -> list[Segment]:
    """Skyline segments of an image: per column the instance of the topmost non-sky pixel, if
    that pixel is a building instance; runs of one instance (``min_cols`` or wider)."""
    H, W = instances.shape
    nonsky = ~sky
    top = np.argmax(nonsky, axis=0)
    has = nonsky.any(axis=0)
    owner = np.where(has, instances[np.minimum(top, H - 1), np.arange(W)], 0)
    out, x = [], 0
    while x < W:
        i = owner[x]
        x1 = x
        while x1 + 1 < W and owner[x1 + 1] == i:
            x1 += 1
        if i > 0 and x1 - x + 1 >= min_cols:
            rows = top[x:x1 + 1].astype(float)
            # the roof: the higher third of the run (a spire or a slope still reads its top)
            t = float(np.median(np.sort(rows)[: max(1, len(rows) // 3)]))
            out.append(Segment((x + x1) / 2.0, float(x1 - x + 1), float(H - t), int(i)))
        x = x1 + 1
    return out


def model_segments(towers: sm.Towers, cam_xy: tuple[float, float], h_cam: float,
                   min_width_deg: float = 0.3, min_elev_deg: float = -2.0) -> list[Segment]:
    """Skyline segments predicted from ``cam_xy``: runs of bearings owned by one tower, left to
    right starting at the widest gap (so a group never straddles the wrap)."""
    elev, own = sm.predicted_outline(towers, cam_xy, h_cam=h_cam, owners=True)
    n = len(own)
    if not (own >= 0).any():
        return []
    # start after the longest stretch without a tower
    empty = own < 0
    start = 0
    if empty.any():
        e2 = np.concatenate([empty, empty])
        best, run, end = 0, 0, 0
        for k, v in enumerate(e2):
            run = run + 1 if v else 0
            if run > best:
                best, end = run, k
        start = (end + 1) % n
    o = np.roll(own, -start)
    ev = np.roll(elev, -start)
    out, k = [], 0
    while k < n:
        t = o[k]
        k1 = k
        while k1 + 1 < n and o[k1 + 1] == t:
            k1 += 1
        w = (k1 - k + 1) * sm.BIN_DEG
        if t >= 0 and w >= min_width_deg and ev[k:k1 + 1].max() > min_elev_deg:
            centre = ((start + (k + k1) / 2.0) * sm.BIN_DEG)      # bearing, may exceed 360
            out.append(Segment(centre, w, float(ev[k:k1 + 1].max()), int(t)))
        k = k1 + 1
    return out


# ------------------------------------------------------------------------ groups

def group_descriptor(segs: list[Segment]) -> np.ndarray:
    """Zoom/pan/tilt-invariant descriptor of segments in left-to-right order."""
    c = np.array([s.centre for s in segs])
    L = c[-1] - c[0]
    w = np.array([s.width for s in segs]) / L
    t = np.array([s.top for s in segs])
    return np.concatenate([(c[1:-1] - c[0]) / L, w, (t[1:] - t[0]) / L]).astype(np.float32)


def groups(segs: list[Segment], size: int = 3, window: int = 5, min_span: float = 0.0):
    """Index tuples of ``size`` segments in order within ``window`` consecutive segments (so a
    spurious or missing segment does not break every group around it)."""
    for i in range(len(segs)):
        for rest in combinations(range(i + 1, min(len(segs), i + window)), size - 1):
            idx = (i, *rest)
            if segs[idx[-1]].centre - segs[i].centre > min_span:
                yield idx


@dataclass
class ModelIndex:
    """Group descriptors of every candidate camera, in one KD-tree."""
    cams: list[tuple[float, float, float]]            # (x, y, h_cam) in the towers' frame
    segs: list[list[Segment]]
    desc: np.ndarray
    ref: np.ndarray                                    # (camera, i, j, k) per descriptor
    tree: object


def build_index(towers: sm.Towers, cams: list[tuple[float, float, float]],
                min_span_deg: float = 1.0, **seg_kw) -> ModelIndex:
    from scipy.spatial import cKDTree

    allsegs, desc, ref = [], [], []
    for ci, (x, y, h) in enumerate(cams):
        segs = model_segments(towers, (x, y), h, **seg_kw)
        allsegs.append(segs)
        for idx in groups(segs, min_span=min_span_deg):
            desc.append(group_descriptor([segs[i] for i in idx]))
            ref.append((ci, *idx))
    desc = np.array(desc, np.float32) if desc else np.zeros((0, 6), np.float32)
    return ModelIndex(cams, allsegs, desc, np.array(ref, np.int32).reshape(-1, 4),
                      cKDTree(desc) if len(desc) else None)


def candidate_cameras(towers: sm.Towers, bbox_nsew, margin_m: float = 2500.0,
                      step_m: float = 150.0, heights=(2.0, 15.0, 40.0),
                      min_clearance_m: float = 60.0) -> list[tuple[float, float, float]]:
    """Grid of camera positions over the region (plus ``margin_m``) at a few heights, not
    inside or against a tower."""
    n, s, e, w = bbox_nsew
    x0, y0 = towers.to_xy(s, w)
    x1, y1 = towers.to_xy(n, e)
    xs = np.arange(x0 - margin_m, x1 + margin_m + 1, step_m)
    ys = np.arange(y0 - margin_m, y1 + margin_m + 1, step_m)
    cx = np.concatenate([v[:, 0] for v in towers.verts])
    cy = np.concatenate([v[:, 1] for v in towers.verts])
    out = []
    for x in xs:
        for y in ys:
            if np.min(np.hypot(cx - x, cy - y)) < min_clearance_m:
                continue
            out.extend((float(x), float(y), float(h)) for h in heights)
    return out


# ------------------------------------------------------------------------ matching

def identify(img_segs: list[Segment], image_width: int, index: ModelIndex, towers: sm.Towers,
             tol: float = 0.06, min_span_px: float = 40.0, top: int = 5,
             heading_bin_deg: float = 2.0, scale_bin: float = 0.08,
             max_hypotheses: int = 400) -> list[Pose]:
    """Camera poses voted by matching groups, best first, with each image segment's tower.

    A match of image group (pixels) to model group (degrees) gives the scale (px per degree)
    from the spans and the bearing of the image centre from the first segments' offset; votes
    are binned per (camera, heading, log scale), and a photo group votes once per bin.
    """
    if index.tree is None or len(img_segs) < 3:
        return []
    votes: dict[tuple, set] = {}
    for gi, idx in enumerate(groups(img_segs, min_span=min_span_px)):
        segs = [img_segs[i] for i in idx]
        d = group_descriptor(segs)
        for r in index.tree.query_ball_point(d, tol):
            ci, a, b, c = index.ref[r]
            ms = index.segs[ci]
            span_img = segs[-1].centre - segs[0].centre
            span_mod = ms[c].centre - ms[a].centre
            s = span_img / span_mod
            if not 3.0 <= s <= 200.0:              # 2-6000 px over 30-90 deg: a plausible zoom
                continue
            heading = (ms[a].centre + (image_width / 2.0 - segs[0].centre) / s) % 360.0
            key = (int(ci), int(round(heading / heading_bin_deg)), int(round(math.log(s) / scale_bin)))
            votes.setdefault(key, set()).add(gi)
    # votes only propose: with millions of model groups some random group always matches
    # (Cartagena photos: true pose 1-7 votes, tied with wrong ones). Each proposal is verified
    # against every building in the image: how many line up with a tower at a consistent top.
    ranked = sorted(votes.items(), key=lambda kv: -len(kv[1]))[:max_hypotheses]
    scored = []
    for (ci, hb, sb), groups_ in ranked:
        s = math.exp(sb * scale_bin)
        heading = hb * heading_bin_deg
        n, ids, heading, s = verify(img_segs, image_width, index.segs[ci], heading, s)
        scored.append((n, len(groups_), ci, heading, s, ids))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    out, seen = [], set()
    for n, nv, ci, heading, s, ids in scored:
        x, y, h = index.cams[ci]
        key = (round(x / 300), round(y / 300), round(heading / 5))
        if key in seen:
            continue
        seen.add(key)
        lat, lon = towers.to_ll(x, y)
        out.append(Pose(ci, lat, lon, h, heading % 360.0, s, nv, ids, n))
        if len(out) >= top:
            break
    return out


def verify(img_segs: list[Segment], image_width: int, model: list[Segment], heading: float,
           px_per_deg: float, iters: int = 2):
    """Buildings that agree with a pose: each image segment's nearest model segment by bearing
    (within half its width + 0.4 deg), kept when its top matches the model top under one
    common vertical offset (the unknown tilt) within max(4 px, 0.35 deg) and widths agree
    within 2x. The pose's heading and scale are re-fitted on the agreeing pairs.
    Returns (count, {instance: (tower, 1)}, heading, px_per_deg)."""
    best = (0, {}, heading, px_per_deg)
    for _ in range(iters + 1):
        pairs = []
        for sg in img_segs:
            b = heading + (sg.centre - image_width / 2.0) / px_per_deg
            near = None
            for m in model:
                d = abs((b - m.centre + 180.0) % 360.0 - 180.0)
                if d <= m.width / 2 + 0.4 and (near is None or d < near[0]):
                    near = (d, m)
            if near is not None:
                m = near[1]
                if 0.5 <= (sg.width / px_per_deg) / m.width <= 2.0:
                    pairs.append((sg, m))
        if len(pairs) < 3:
            break
        off = np.array([sg.top - px_per_deg * m.top for sg, m in pairs])
        tol = max(4.0, 0.35 * px_per_deg)
        ok = np.abs(off - np.median(off)) <= tol
        inl = [p for p, k in zip(pairs, ok, strict=True) if k]
        if len(inl) > best[0]:
            best = (len(inl), {sg.ident: (m.ident, 1) for sg, m in inl}, heading, px_per_deg)
        if len(inl) < 3:
            break
        # re-fit bearing = a + x / s on the agreeing pairs
        xs = np.array([sg.centre for sg, _ in inl])
        bs = np.array([m.centre for _, m in inl])
        bs = bs[0] + ((bs - bs[0] + 180.0) % 360.0 - 180.0)
        A = np.column_stack([np.ones_like(xs), xs])
        (a, inv_s), *_ = np.linalg.lstsq(A, bs, rcond=None)
        if inv_s <= 0:
            break
        px_per_deg = 1.0 / inv_s
        heading = a + (image_width / 2.0) * inv_s
    return best
