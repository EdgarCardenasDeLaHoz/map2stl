/**
 * modules/export/print-scale.js — pure print-size arithmetic for the Extrude tab.
 *
 * No DOM, no window: imported by model-viewer.js, dem-main.js and the Vue
 * ModelContainer, and unit-tested in tests/js/printScale.test.js.
 *
 * modelScale() mirrors city2stl/city_model.py:choose_scale (the preview does
 * not report its scale, so the client recomputes it from the same inputs).
 * piecesNeeded() mirrors app/server/core/puzzle.py:plan_grid.
 */

// Same constants as city2stl/city_model.py (_M_PER_DEG_LAT/_LON, AUTO_TRUE_SCALE_MAX_KM)
// and window.GEO_M_PER_DEG_* in ui-helpers.js.
const M_PER_DEG_LAT = 110540;
const M_PER_DEG_LON = 111320;
export const AUTO_TRUE_SCALE_MAX_KM = 20;
/**
 * Below this elevation range (m) fit-to-height has no relief to fit, so the
 * vertical exaggeration is meaningless (30 mm / 1e-6 m gave "3,662,321,645×").
 */
const MIN_FIT_RELIEF_M = 0.01;
/** Margin taken off the bed when it sets the default puzzle piece size. */
const BED_MARGIN_MM = 10;

/** Bbox diagonal in km, the flat-earth formula city_model.bbox_diagonal_km uses. */
export function bboxDiagonalKm(bbox) {
    const latMid = ((bbox.north + bbox.south) / 2) * Math.PI / 180;
    const dy = (bbox.north - bbox.south) * M_PER_DEG_LAT;
    const dx = (bbox.east - bbox.west) * M_PER_DEG_LON * Math.cos(latMid);
    return Math.hypot(dx, dy) / 1000;
}

/**
 * Horizontal and vertical scale of the printed model.
 *
 * @param {Object} p
 * @param {{north,south,east,west}} p.bbox
 * @param {number} p.cols          DEM width in pixels
 * @param {number} p.rows          DEM height in pixels
 * @param {number} p.mmPerPx       #mmPerPixel
 * @param {'auto'|'true'|'fit'} [p.zMode='auto']   #exportZMode
 * @param {number} [p.exaggeration=1]              #exportExaggeration
 * @param {number} [p.fitHeightMm=30]              #exportModelHeight
 * @param {number} [p.elevMin=0]   lowest DEM elevation (m), used by fit mode
 * @param {number} [p.elevMax=0]   highest DEM elevation (m), used by fit mode
 * @returns {null | {mPerPx:number, mPerMm:number, scaleDenominator:number,
 *                   zMode:'true'|'fit', zMmPerM:number, verticalExaggeration:number,
 *                   widthMm:number, depthMm:number, flatRelief:boolean}}
 *   mPerMm: ground metres per model millimetre ("1 mm = X m");
 *   verticalExaggeration: vertical scale over horizontal scale (1 = true scale);
 *   flatRelief: fit mode with (near-)zero or unknown elevation range, so
 *     verticalExaggeration is not meaningful and should not be displayed.
 */
export function modelScale({
    bbox, cols, rows, mmPerPx,
    zMode = 'auto', exaggeration = 1, fitHeightMm = 30, elevMin = 0, elevMax = 0,
}) {
    if (!bbox || !(cols > 0) || !(rows > 0) || !(mmPerPx > 0)) return null;
    const latMid = ((bbox.north + bbox.south) / 2) * Math.PI / 180;
    const mPxY = Math.abs(bbox.north - bbox.south) * M_PER_DEG_LAT / rows;
    const mPxX = Math.abs(bbox.east - bbox.west) * M_PER_DEG_LON * Math.cos(latMid) / cols;
    const mPerPx = (mPxX + mPxY) / 2;
    if (!(mPerPx > 0)) return null;

    const exag = exaggeration > 0 ? exaggeration : 1;
    let mode = zMode;
    if (mode !== 'true' && mode !== 'fit') {
        mode = bboxDiagonalKm(bbox) < AUTO_TRUE_SCALE_MAX_KM ? 'true' : 'fit';
    }
    const trueZ = mmPerPx / mPerPx;   // mm per metre at true scale
    const relief = elevMax - elevMin;
    const flatRelief = mode === 'fit' && !(relief >= MIN_FIT_RELIEF_M);
    const zMmPerM = mode === 'true'
        ? trueZ * exag
        : fitHeightMm / Math.max(elevMax - elevMin, 1e-6) * exag;

    return {
        mPerPx,
        mPerMm: mPerPx / mmPerPx,
        scaleDenominator: Math.round(mPerPx * 1000 / mmPerPx),
        zMode: mode,
        zMmPerM,
        verticalExaggeration: zMmPerM / trueZ,
        widthMm: cols * mmPerPx,
        depthMm: rows * mmPerPx,
        flatRelief,
    };
}

/** "3.5 m", "12 m", "1.2 km" — the ground length one model millimetre covers. */
export function formatGroundLength(m) {
    if (!Number.isFinite(m)) return '—';
    if (m >= 1000) return `${(m / 1000).toFixed(m >= 10000 ? 0 : 1)} km`;
    if (m >= 10) return `${Math.round(m)} m`;
    return `${m.toFixed(m >= 1 ? 1 : 2)} m`;
}

/**
 * Bed size in mm from the #bedSizeSelect value ("220x220" or "custom"). Default: Ender 220 x 220.
 * @returns {{w:number, h:number}}
 */
/** Default printer bed (#bedSizeSelect value): Ender 220 x 220 (user, 2026-10-01). */
export const DEFAULT_BED = '220x220';

export function parseBedSize(value, customW, customH) {
    if (value === 'custom') {
        const w = parseFloat(customW);
        const h = parseFloat(customH);
        return { w: w > 0 ? w : 220, h: h > 0 ? h : 220 };
    }
    const [w, h] = String(value || DEFAULT_BED).split('x').map(Number);
    return (w > 0 && h > 0) ? { w, h } : { w: 220, h: 220 };
}

/** Default largest puzzle piece for a bed: the shorter side less a margin. */
export function defaultPieceMm(bed, marginMm = BED_MARGIN_MM) {
    return Math.max(30, Math.floor(Math.min(bed.w, bed.h) - marginMm));
}

/**
 * Does a widthMm × depthMm model fit the bed (either orientation), and if not,
 * which grid of pieces no larger than pieceMm does the city puzzle cut?
 * @returns {{fits:boolean, cols:number, rows:number, pieces:number}}
 */
export function piecesNeeded(widthMm, depthMm, bed, pieceMm) {
    const fits = (widthMm <= bed.w && depthMm <= bed.h) || (widthMm <= bed.h && depthMm <= bed.w);
    const piece = pieceMm > 0 ? pieceMm : defaultPieceMm(bed);
    const cols = fits ? 1 : Math.max(1, Math.ceil(widthMm / piece));
    const rows = fits ? 1 : Math.max(1, Math.ceil(depthMm / piece));
    return { fits, cols, rows, pieces: cols * rows };
}

/**
 * mm per DEM pixel that makes a cols × rows grid fill the bed, less BED_MARGIN_MM on
 * each axis, in whichever orientation gives the larger model (the slicer can turn it).
 * Rounded down to 0.01 so the result never overhangs. Used by the Extrude "Fill bed"
 * action and ✨ Make it printable.
 * @returns {number} 0 when the grid is empty
 */
export function fillBedMmPerPx(cols, rows, bed, marginMm = BED_MARGIN_MM) {
    if (!(cols > 0 && rows > 0)) return 0;
    const w = Math.max(1, bed.w - marginMm);
    const h = Math.max(1, bed.h - marginMm);
    const best = Math.max(Math.min(w / cols, h / rows), Math.min(h / cols, w / rows));
    return Math.floor(best * 100) / 100;
}

/**
 * Printed width × depth (mm) of a box of ground size widthKm × heightKm sized to fill the bed
 * less BED_MARGIN_MM, turned if that is bigger (as fillBedMmPerPx does for a DEM grid). Used for
 * the size label while drawing a new region, before any DEM exists.
 * @returns {{w:number, h:number}} whole mm
 */
export function bedFitMm(widthKm, heightKm, bed, marginMm = BED_MARGIN_MM) {
    if (!(widthKm > 0 && heightKm > 0)) return { w: 0, h: 0 };
    const bw = Math.max(1, bed.w - marginMm);
    const bh = Math.max(1, bed.h - marginMm);
    const k = Math.max(Math.min(bw / widthKm, bh / heightKm), Math.min(bh / widthKm, bw / heightKm));
    return { w: Math.floor(widthKm * k), h: Math.floor(heightKm * k) };
}
