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
