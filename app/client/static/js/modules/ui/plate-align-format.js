/**
 * plate-align-format.js — pure logic for the Plate align page
 * (vue/components/views/PlateAlignPage.vue): the placement maths and the texts.
 * No DOM, no fetch.
 *
 * A placement is a 2x3 affine "M" mapping plate (STL) pixels to map (OSM) pixels, the pipeline
 * convention: [[a, c, tx], [b, d, ty]], x' = a x + c y + tx, y' = b x + d y + ty. The map raster
 * has row 0 = north, so +y on screen is south.
 */

export const IDENTITY = [[1, 0, 0], [0, 1, 0]];

/** Apply a 2x3 matrix to a point. */
export function apply(M, x, y) {
    return [M[0][0] * x + M[0][1] * y + M[0][2], M[1][0] * x + M[1][1] * y + M[1][2]];
}

/** Inverse of a 2x3 affine; throws on a singular matrix. */
export function invert2x3(M) {
    const a = M[0][0], c = M[0][1], tx = M[0][2];
    const b = M[1][0], d = M[1][1], ty = M[1][2];
    const det = a * d - c * b;
    if (!det) throw new Error('singular transform');
    const ia = d / det, ic = -c / det, ib = -b / det, id = a / det;
    return [[ia, ic, -(ia * tx + ic * ty)], [ib, id, -(ib * tx + id * ty)]];
}

/** Similarity-style readout of a placement: scale, rotation (deg), translation, flip. */
export function decompose(M) {
    const det = M[0][0] * M[1][1] - M[0][1] * M[1][0];
    return {
        scale: Math.hypot(M[0][1], M[1][1]),
        scale_x: Math.hypot(M[0][0], M[1][0]),
        rot_deg: Math.atan2(-M[0][1], M[1][1]) * 180 / Math.PI,
        tx: M[0][2], ty: M[1][2],
        flip: det < 0,
    };
}

/** Plate corners (TL, TR, BR, BL) in map pixels. */
export function plateCorners(M, sw, sh) {
    return [[0, 0], [sw, 0], [sw, sh], [0, sh]].map(([x, y]) => apply(M, x, y));
}

/** Plate centre in map pixels. */
export function plateCentre(M, sw, sh) {
    return apply(M, sw / 2, sh / 2);
}

/** Shift the placement by (dx, dy) map pixels. */
export function translate(M, dx, dy) {
    return [[M[0][0], M[0][1], M[0][2] + dx], [M[1][0], M[1][1], M[1][2] + dy]];
}

/** Rotate by `deg` about the map point (cx, cy). Positive turns clockwise on screen (y down). */
export function rotateAbout(M, cx, cy, deg) {
    const r = deg * Math.PI / 180, cs = Math.cos(r), sn = Math.sin(r);
    const R = [[cs, -sn], [sn, cs]];
    const A = [
        [R[0][0] * M[0][0] + R[0][1] * M[1][0], R[0][0] * M[0][1] + R[0][1] * M[1][1]],
        [R[1][0] * M[0][0] + R[1][1] * M[1][0], R[1][0] * M[0][1] + R[1][1] * M[1][1]],
    ];
    const px = M[0][2] - cx, py = M[1][2] - cy;
    return [[A[0][0], A[0][1], cx + R[0][0] * px + R[0][1] * py],
        [A[1][0], A[1][1], cy + R[1][0] * px + R[1][1] * py]];
}

/** Scale along the plate's own axes by (fx, fy), keeping the plate centre where it is. */
export function scaleLocal(M, sw, sh, fx, fy) {
    const c = plateCentre(M, sw, sh);
    const A = [[M[0][0] * fx, M[0][1] * fy], [M[1][0] * fx, M[1][1] * fy]];
    const px = sw / 2, py = sh / 2;
    return [[A[0][0], A[0][1], c[0] - (A[0][0] * px + A[0][1] * py)],
        [A[1][0], A[1][1], c[1] - (A[1][0] * px + A[1][1] * py)]];
}

/** Mirror the plate left-right about its centre. */
export function flipPlate(M, sw, sh) {
    return scaleLocal(M, sw, sh, -1, 1);
}

/** Uniform scale about the plate centre. */
export function scaleAbout(M, sw, sh, f) {
    return scaleLocal(M, sw, sh, f, f);
}

/** Rotate about the plate's own centre. */
export function rotatePlate(M, sw, sh, deg) {
    const c = plateCentre(M, sw, sh);
    return rotateAbout(M, c[0], c[1], deg);
}

/**
 * Corner drag: where the pointer started and is now (both in map pixels), the plate scales about
 * its centre. Locked: one factor from the distance to the centre. Unlocked: one factor per plate
 * axis. Factors are kept within 0.2..5 of the start so a wild drag cannot flip or collapse it.
 */
export function cornerScale(M0, sw, sh, start, now, lock) {
    const c = plateCentre(M0, sw, sh);
    const clamp = (f) => Math.min(5, Math.max(0.2, f));
    if (lock) {
        const d0 = Math.hypot(start[0] - c[0], start[1] - c[1]);
        const d1 = Math.hypot(now[0] - c[0], now[1] - c[1]);
        if (d0 < 1e-9) return M0;
        const f = clamp(d1 / d0);
        return scaleLocal(M0, sw, sh, f, f);
    }
    const inv = invert2x3(M0);
    const u0 = apply(inv, start[0], start[1]);
    const u1 = apply(inv, now[0], now[1]);
    const cx = sw / 2, cy = sh / 2;
    const fx = Math.abs(u0[0] - cx) < 1e-9 ? 1 : clamp((u1[0] - cx) / (u0[0] - cx));
    const fy = Math.abs(u0[1] - cy) < 1e-9 ? 1 : clamp((u1[1] - cy) / (u0[1] - cy));
    return scaleLocal(M0, sw, sh, Math.abs(fx), Math.abs(fy));
}

/** Rotation drag: angle (deg) from the centre-to-start direction to the centre-to-now one. */
export function rotateDelta(centre, start, now) {
    const a0 = Math.atan2(start[1] - centre[1], start[0] - centre[0]);
    const a1 = Math.atan2(now[1] - centre[1], now[0] - centre[0]);
    let d = (a1 - a0) * 180 / Math.PI;
    while (d > 180) d -= 360;
    while (d < -180) d += 360;
    return d;
}

/** Position of the rotate handle: `gap` map pixels beyond the middle of the top edge. */
export function rotateHandle(M, sw, sh, gap) {
    const top = apply(M, sw / 2, 0);
    const c = plateCentre(M, sw, sh);
    const dx = top[0] - c[0], dy = top[1] - c[1];
    const len = Math.hypot(dx, dy) || 1;
    return [top[0] + dx / len * gap, top[1] + dy / len * gap];
}

/** Map-pixel offset of the plate centre between two placements, in metres east and north. */
export function offsetMetres(M, ref, sw, sh, cellM) {
    const a = plateCentre(M, sw, sh), b = plateCentre(ref, sw, sh);
    return { east: (a[0] - b[0]) * cellM, north: -(a[1] - b[1]) * cellM };
}

function fmt1(v) { return (Math.round(v * 10) / 10).toFixed(1); }

/** "Offset 12.4 m E, 3.1 m N · rotation −6.0° · scale 1.012" against the reference placement. */
export function offsetText(M, ref, sw, sh, cellM) {
    if (!M || !ref) return '';
    const o = offsetMetres(M, ref, sw, sh, cellM);
    const p = decompose(M), q = decompose(ref);
    const rot = p.rot_deg - q.rot_deg;
    const e = `${fmt1(Math.abs(o.east))} m ${o.east >= 0 ? 'E' : 'W'}`;
    const n = `${fmt1(Math.abs(o.north))} m ${o.north >= 0 ? 'N' : 'S'}`;
    const r = `${rot < 0 ? '−' : ''}${fmt1(Math.abs(rot))}°`;
    const s = q.scale ? (p.scale / q.scale).toFixed(3) : '1.000';
    return `Offset ${e}, ${n} · rotation ${r} · scale ${s}`;
}

/** Overlap as the card shows it: "0.61", or "—". */
export function fmtIou(v) {
    return Number.isFinite(v) ? v.toFixed(2) : '—';
}

/** Colour class for an overlap value: good from 0.5, fair from 0.25. */
export function iouTone(v) {
    if (!Number.isFinite(v)) return '';
    return v >= 0.5 ? 'good' : v >= 0.25 ? 'warn' : 'bad';
}

/** "2.3 m" mean edge gap, or "—". */
export function fmtGap(v) {
    return Number.isFinite(v) ? `${v.toFixed(1)} m` : '—';
}

/** "lat, lon" or "lat lon" -> [lat, lon]; null when blank; throws when present but unusable. */
export function parseCenter(text) {
    const s = (text || '').trim();
    if (!s) return null;
    const parts = s.split(/[,\s]+/).map(parseFloat);
    if (parts.length !== 2 || !parts.every(Number.isFinite)) {
        throw new Error("centre must be 'lat, lon'");
    }
    if (Math.abs(parts[0]) > 90 || Math.abs(parts[1]) > 180) {
        throw new Error('centre out of range (lat +/-90, lon +/-180)');
    }
    return parts;
}

/** "window centre 41.38240, 2.17943" from a [N, S, E, W] box, or "". */
export function windowCentreText(bbox) {
    if (!bbox || bbox.length < 4) return '';
    const [N, S, E, W] = bbox;
    return `${((N + S) / 2).toFixed(5)}, ${((E + W) / 2).toFixed(5)}`;
}

/**
 * The saved record for a placement. Same fields as the standalone drag-align tool writes, so
 * either tool reads the other's files: `transform` is STL px -> OSM px; `ui.osm_to_stl` is its
 * inverse as scale/rotation/translation (omitted for a mirrored plate, which that form cannot hold).
 */
export function buildGroundTruth(meta, M, layer, now = new Date()) {
    const out = {
        city: meta.city, region: meta.region, slug: meta.slug,
        resolution: meta.resolution, cell_size_m: meta.cell_size_m,
        osm_bbox_nsew: meta.bbox_nsew,
        layer_used: layer,
        transform: M,
        transform_params: decompose(M),
        pipeline_guess: meta.guess,
        exported_at: now.toISOString(),
    };
    const p = decompose(M);
    out.ui = { flip: p.flip };
    if (!p.flip) {
        const N = invert2x3(M);
        const q = decompose(N);
        out.ui.osm_to_stl = { scale: q.scale, rot_deg: q.rot_deg, tx: q.tx, ty: q.ty };
        out.ui.osm_to_stl_matrix = N;
    }
    return out;
}

/** The placement (STL px -> OSM px) a saved record holds, or null when it carries none. */
export function placementFromSaved(gt) {
    if (gt && Array.isArray(gt.transform) && gt.transform.length === 2) return gt.transform;
    const u = gt && gt.ui && gt.ui.osm_to_stl;
    if (u) {
        const r = u.rot_deg * Math.PI / 180, c = Math.cos(r) * u.scale, s = Math.sin(r) * u.scale;
        return invert2x3([[c, -s, u.tx], [s, c, u.ty]]);
    }
    return null;
}

/** Fit a view (scale k, offset vx, vy) so the given map-pixel points fill w x h with a margin. */
export function fitPoints(points, w, h, margin = 0.94) {
    const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const k = Math.min(w / Math.max(x1 - x0, 1e-9), h / Math.max(y1 - y0, 1e-9)) * margin;
    return { k, vx: w / 2 - k * (x0 + x1) / 2, vy: h / 2 - k * (y0 + y1) / 2 };
}

/** Map pixel -> screen. */
export function toScreen(view, x, y) {
    return [view.vx + view.k * x, view.vy + view.k * y];
}

/** Screen -> map pixel. */
export function fromScreen(view, x, y) {
    return [(x - view.vx) / view.k, (y - view.vy) / view.k];
}

/** Zoom about a screen point; k stays within [kMin, kMax]. */
export function zoomView(view, sx, sy, factor, kMin = 0.05, kMax = 80) {
    const k = Math.min(kMax, Math.max(kMin, view.k * factor));
    const f = k / view.k;
    return { k, vx: sx - (sx - view.vx) * f, vy: sy - (sy - view.vy) * f };
}

/** The handle under a screen point: 'rotate', 'c0'..'c3', 'body', or null. */
export function hitPlate(view, M, sw, sh, sx, sy, grab = 9, rotGap = 28) {
    const sc = plateCorners(M, sw, sh).map(([x, y]) => toScreen(view, x, y));
    const rh = rotateHandle(M, sw, sh, rotGap / view.k);
    const rs = toScreen(view, rh[0], rh[1]);
    if (Math.hypot(sx - rs[0], sy - rs[1]) <= grab + 2) return 'rotate';
    for (let i = 0; i < 4; i++) {
        if (Math.hypot(sx - sc[i][0], sy - sc[i][1]) <= grab) return `c${i}`;
    }
    // point in the quad (even-odd)
    let inside = false;
    for (let i = 0, j = 3; i < 4; j = i++) {
        const [xi, yi] = sc[i], [xj, yj] = sc[j];
        if ((yi > sy) !== (yj > sy) && sx < (xj - xi) * (sy - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside ? 'body' : null;
}

/** Status line for a refine result (the ECC job's `result`). */
export function refineText(r) {
    if (!r) return '';
    if (!r.improved) {
        return `Nothing better than your placement was found (overlap ${fmtIou(r.iou_before)}). Nudge it closer and try again.`;
    }
    return `Refined: overlap ${fmtIou(r.iou_before)} → ${fmtIou(r.iou_after)}, moved `
        + `${(r.delta_translation_px ?? 0).toFixed(1)} px, ${(r.delta_rot_deg ?? 0).toFixed(2)}°.`;
}

/** Keyboard step for the arrow, rotate and scale keys. Returns an op or null. */
export function keyOp(key, shift) {
    const step = shift ? 5 : 0.5, rstep = shift ? 1 : 0.1;
    switch (key) {
        case 'ArrowLeft': return { op: 'move', dx: -step, dy: 0 };
        case 'ArrowRight': return { op: 'move', dx: step, dy: 0 };
        case 'ArrowUp': return { op: 'move', dx: 0, dy: -step };
        case 'ArrowDown': return { op: 'move', dx: 0, dy: step };
        case 'q': case 'Q': return { op: 'rotate', deg: -rstep };
        case 'e': case 'E': return { op: 'rotate', deg: rstep };
        case '+': case '=': return { op: 'scale', f: 1.005 };
        case '-': case '_': return { op: 'scale', f: 1 / 1.005 };
        default: return null;
    }
}
