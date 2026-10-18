/**
 * river-style.js — how rivers look on the Edit map: the pixel carve
 * (water-hydrology-combined.js) and the river lines (river-lines.js).
 *
 * Light cyan with a dark edge, so a river reads over land cover, satellite and the
 * terrain colours alike (user 2026-10-17: the old dark blue barely showed). "Colour
 * rivers by Size" (#hydroColorMode = 'order') keeps one colour per Strahler order.
 *
 * Pure: no window, no DOM, so vitest imports it directly.
 */

/** River colour, "Colour rivers by Depth": light cyan. */
export const RIVER_RGB = Object.freeze([110, 230, 255]);
/** Edge drawn around every river pixel / line: dark navy. */
export const RIVER_EDGE_RGB = Object.freeze([0, 35, 70]);
/** Edge opacity, 0–255. */
export const RIVER_EDGE_ALPHA = 190;
/** Open water (lakes, sea) on the hydrology grid: the old mid blue, half see-through. */
export const OPEN_WATER_RGB = Object.freeze([0, 100, 255]);
export const OPEN_WATER_ALPHA = 150;

/** Colour per Strahler order 1..10 (viridis steps: small streams dark, trunk rivers yellow). */
export const HYDRO_ORDER_COLORS = Object.freeze([
    [68, 1, 84], [72, 40, 120], [62, 73, 137], [49, 104, 142], [38, 130, 142],
    [31, 158, 137], [53, 183, 121], [110, 206, 88], [181, 222, 43], [253, 231, 37],
]);

/** Colour of Strahler order `o` (1-based, clamped to the table). */
export function orderColor(o) {
    const i = Math.max(1, Math.min(HYDRO_ORDER_COLORS.length, o | 0)) - 1;
    return HYDRO_ORDER_COLORS[i];
}

/**
 * Colour of one hydrology pixel, or null for "draw nothing".
 *
 * @param {number} v          carve value (negative = deeper; 0 = no river)
 * @param {number} minD       deepest carve of the grid (negative), for the depth ramp
 * @param {number|null} o     Strahler order of the pixel (null when the grid has none)
 * @param {number} waterCode  order value that marks open water
 * @param {boolean} byOrder   "Colour rivers by Size"
 * @returns {[number, number, number, number]|null} r, g, b, alpha (0–255)
 */
export function riverPixelColor(v, minD, o, waterCode, byOrder) {
    if (!v) return null;
    if (o != null && o === waterCode) return [...OPEN_WATER_RGB, OPEN_WATER_ALPHA];
    if (byOrder) {
        if (o == null || o <= 0) return null;
        return [...orderColor(o), 245];
    }
    // Deeper river => more opaque; the shallowest still clearly visible.
    const t = minD ? Math.min(1, Math.max(0, v / minD)) : 1;
    return [...RIVER_RGB, Math.round(200 + t * 55)];   // 200–255
}

/**
 * The edge pixels of a river mask: empty pixels with a river pixel above, below, left
 * or right. Drawn dark, they give a one-pixel river a dark outline on any background.
 *
 * @param {Uint8Array} mask  1 = river pixel (open water excluded), row-major, w × h
 * @param {number} w
 * @param {number} h
 * @returns {Uint8Array} 1 = edge pixel
 */
export function riverEdgeMask(mask, w, h) {
    const edge = new Uint8Array(w * h);
    for (let y = 0; y < h; y++) {
        const row = y * w;
        for (let x = 0; x < w; x++) {
            const i = row + x;
            if (mask[i]) continue;
            if ((x > 0 && mask[i - 1]) || (x < w - 1 && mask[i + 1])
                || (y > 0 && mask[i - w]) || (y < h - 1 && mask[i + w])) edge[i] = 1;
        }
    }
    return edge;
}

/**
 * Screen width of a river line, CSS px: grows with Strahler order, and with the zoom
 * (half as fast, so trunk rivers do not swamp a zoomed-in view). At least 1 px.
 *
 * @param {number} order  Strahler order (1–10)
 * @param {number} [zoom=1]  Edit map zoom (stackZoom.scale)
 */
export function riverLineWidthPx(order, zoom = 1) {
    const base = 0.6 + 0.35 * Math.max(1, Math.min(10, order || 1));   // 0.95 … 4.1
    return Math.max(1, base * Math.sqrt(Math.max(1, zoom)));
}

/** "rgb(r, g, b)" of an [r, g, b] triple. */
export function rgb([r, g, b]) {
    return `rgb(${r}, ${g}, ${b})`;
}
