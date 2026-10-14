/**
 * modules/export/puzzle-cuts.js — puzzle cut positions (pure, no DOM).
 *
 * Edges are mm from the model's west (columns) or south (rows) edge, the way
 * the server's puzzle spec takes them (`col_edges_mm` / `row_edges_mm`,
 * app/server/core/puzzle.py): `[0, e1, ..., size]`, strictly increasing.
 * The Extrude preview lets the user drag the interior ones
 * (model-viewer.js); this module holds the arithmetic so it is unit-tested.
 */

/** Even split of `size` into `n` pieces: n + 1 positions. */
export function evenEdges(size, n) {
    const k = Math.max(1, Math.round(n));
    return Array.from({ length: k + 1 }, (_, i) => (i === k ? size : (size * i) / k));
}

/**
 * Smallest piece the server accepts for these knobs:
 * knob_depth + knob_width / 2 + 2 * clearance < piece / 2 (numpy2stl jigsaw_outlines),
 * plus 1 mm of slack.
 */
export function minPieceMm(knobWidth, knobDepth, clearance) {
    return 2 * (knobDepth + knobWidth / 2 + 2 * clearance) + 1;
}

/** Interior edge index nearest to `pos` within `tol`, or -1. */
export function nearestEdge(edges, pos, tol) {
    let best = -1;
    let bestD = tol;
    for (let i = 1; i < edges.length - 1; i++) {
        const d = Math.abs(edges[i] - pos);
        if (d <= bestD) { best = i; bestD = d; }
    }
    return best;
}

/** Copy of `edges` with interior edge `i` at `pos`, kept `minGap` from its neighbours. */
export function moveEdge(edges, i, pos, minGap = 0) {
    const out = edges.slice();
    if (i <= 0 || i >= edges.length - 1) return out;
    const lo = edges[i - 1] + minGap;
    const hi = edges[i + 1] - minGap;
    out[i] = lo > hi ? (edges[i - 1] + edges[i + 1]) / 2 : Math.min(hi, Math.max(lo, pos));
    return out;
}

/** Key identifying the grid an edge set belongs to (dragged edges reset when it changes). */
export function gridKey(cols, rows, widthMm, depthMm) {
    return `${cols}x${rows}@${widthMm.toFixed(1)}x${depthMm.toFixed(1)}`;
}

/** True when `edges` differ from the even split (worth sending with the export). */
export function isCustom(edges) {
    const even = evenEdges(edges[edges.length - 1], edges.length - 1);
    return edges.some((e, i) => Math.abs(e - even[i]) > 0.05);
}

/** Round to 0.1 mm for the request / display. */
export function roundEdges(edges) {
    return edges.map(e => Math.round(e * 10) / 10);
}

// ─────────────────────────────────────────────────────────────────────────────
// Jigsaw outline (the shape the server actually cuts)
// ─────────────────────────────────────────────────────────────────────────────

/** The knob outlines numpy2stl offers (applications/puzzle.py KNOB_SHAPES). */
export const KNOB_SHAPES = ['classic', 'dovetail', 'rectangular'];

/**
 * Tongue outline in the edge frame, as `[[u, v], ...]` from the root at
 * v < 0 round to the root at v > 0: `u` runs 0..`depth` away from the cut,
 * `v` across it. `halfWidth` is half the widest part.
 *
 * Same three shapes as numpy2stl's `_tongue`: `classic` is a rounded head
 * (an ellipse 0.9 * depth by the full width) on a neck half as wide,
 * `dovetail` widens from 0.6x at the cut to the full width at the tip,
 * `rectangular` is a plain tab.
 */
export function tonguePath(shape, halfWidth, depth, segments = 16) {
    const k2 = halfWidth;
    const d = depth;
    if (shape === 'rectangular') return [[0, -k2], [d, -k2], [d, k2], [0, k2]];
    if (shape === 'dovetail') return [[0, -0.6 * k2], [d, -k2], [d, k2], [0, 0.6 * k2]];
    if (shape !== 'classic') throw new Error(`unknown knob shape ${shape}`);
    // Neck out to u = 0.55 d at half width, then the head's ellipse from the
    // point where it leaves the neck (-150°) round the tip to +150°.
    const pts = [[0, -0.5 * k2]];
    const t0 = -Math.PI * 5 / 6;
    const n = Math.max(4, Math.round(segments));
    for (let i = 0; i <= n; i++) {
        const t = t0 + (2 * Math.PI * 5 / 6) * (i / n);
        pts.push([0.55 * d + 0.45 * d * Math.cos(t), k2 * Math.sin(t)]);
    }
    pts.push([0, 0.5 * k2]);
    return pts;
}

/**
 * True when the server would accept this knob on pieces `pieceW` × `pieceH` mm
 * (numpy2stl `jigsaw_outlines`: depth + width / 2 + 2 * clearance < min / 2).
 */
export function knobFits(knobWidth, knobDepth, clearance, pieceW, pieceH) {
    const k2 = knobWidth / 2;
    return Math.min(k2, knobDepth) > 0
        && knobDepth + k2 + 2 * clearance < Math.min(pieceW, pieceH) / 2;
}

/**
 * One cut as a model-mm polyline: the shared edge from `lo` to `hi` with the
 * tongue crossing it at `centre`.
 *
 * `axis` 0 is a vertical cut at x = `at` (`lo`/`hi` are y), axis 1 a horizontal
 * cut at y = `at` (`lo`/`hi` are x); `sign` is the direction the tongue points
 * along that axis. A zero-size knob gives the straight edge.
 */
export function cutPolyline({ axis, at, lo, hi, centre, sign, shape = 'classic',
    halfWidth = 0, depth = 0, segments = 16 }) {
    const frame = axis === 0
        ? ([u, v]) => [at + sign * u, centre + v]
        : ([u, v]) => [centre + v, at + sign * u];
    if (Math.min(halfWidth, depth) <= 0) return [frame([0, lo - centre]), frame([0, hi - centre])];
    return [frame([0, lo - centre]),
        ...tonguePath(shape, halfWidth, depth, segments).map(frame),
        frame([0, hi - centre])];
}

/**
 * Every interior cut of the grid as a model-mm polyline, following the
 * server's checkerboard tongue direction (numpy2stl `jigsaw_outlines`):
 * vertical cuts point +x in even rows and -x in odd rows, horizontal cuts
 * +y in even columns and -y in odd columns.
 *
 * `{ polylines, knobs }` — `knobs` is false when the knob does not fit the
 * smallest piece (or is zero), in which case the cuts are straight lines, as
 * the server would refuse the knob anyway.
 */
export function jigsawCuts(colEdges, rowEdges, { shape = 'classic', knobWidth = 0,
    knobDepth = 0, clearance = 0, segments = 16 } = {}) {
    const cols = colEdges.length - 1;
    const rows = rowEdges.length - 1;
    const minSpan = (e) => e.slice(1).reduce((m, v, i) => Math.min(m, v - e[i]), Infinity);
    const knobs = KNOB_SHAPES.includes(shape)
        && knobFits(knobWidth, knobDepth, clearance, minSpan(colEdges), minSpan(rowEdges));
    const knob = knobs ? { shape, halfWidth: knobWidth / 2, depth: knobDepth, segments } : {};
    const polylines = [];
    for (let r = 0; r < rows; r++) {
        const yc = (rowEdges[r] + rowEdges[r + 1]) / 2;
        for (let q = 0; q < cols - 1; q++) {
            polylines.push(cutPolyline({
                axis: 0, at: colEdges[q + 1], lo: rowEdges[r], hi: rowEdges[r + 1],
                centre: yc, sign: r % 2 === 0 ? 1 : -1, ...knob,
            }));
        }
    }
    for (let q = 0; q < cols; q++) {
        const xc = (colEdges[q] + colEdges[q + 1]) / 2;
        for (let r = 0; r < rows - 1; r++) {
            polylines.push(cutPolyline({
                axis: 1, at: rowEdges[r + 1], lo: colEdges[q], hi: colEdges[q + 1],
                centre: xc, sign: q % 2 === 0 ? 1 : -1, ...knob,
            }));
        }
    }
    return { polylines, knobs };
}
