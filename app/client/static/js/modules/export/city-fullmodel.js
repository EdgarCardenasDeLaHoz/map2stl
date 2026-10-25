/**
 * city-fullmodel.js — the finished City Model's parts in the Extrude 3D view.
 *
 * After the quick view (city-quickview.js) the viewer starts the same City Model build
 * the Download button runs and, when it is done, replaces the quick-view buildings with
 * the model's own parts (terrain, buildings, roads, water, green, …) as they will print. The
 * preview terrain is hidden meanwhile: the model's terrain part is the same ground with its
 * base, and the buildings' buried walls end inside it.
 *
 * Pure: parsing and coordinate mapping only (vitest imports it).
 *
 * File layout (numpy2stl.io.write_parts_file): uint32 header
 * length, UTF-8 JSON header {parts: [{name, vertices, faces}]} padded to 4 bytes, then
 * per part float32 vertices (model mm: x east, y north, z up) and uint32 faces.
 */

/** Parts with their own colour; anything else is drawn light grey.
 *
 * Muted on purpose: lit in the 3D view, saturated layer colours read as neon and
 * hide the buildings, which are the part the eye needs to follow.
 */
const PART_COLORS = {
    roads: '#b08b6a', waterways: '#6b93b0', green: '#8aa47a', railways: '#6e6e6e',
    trails: '#a8886a', trees: '#5f8a4e',
};
const BUILDING_PARTS = new Set(['buildings', 'churches', 'towers', 'walls', 'fortifications']);

/**
 * @param {ArrayBuffer} buffer
 * @returns {{name: string, positions: Float32Array, indices: Uint32Array}[]}
 */
export function parseModelParts(buffer) {
    const n = new DataView(buffer).getUint32(0, true);
    const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, n)));
    let off = 4 + n;
    return header.parts.map(({ name, vertices, faces }) => {
        // slice(): own, aligned buffers (the parts follow each other in one file)
        const positions = new Float32Array(buffer.slice(off, off + vertices * 12));
        off += vertices * 12;
        const indices = new Uint32Array(buffer.slice(off, off + faces * 12));
        off += faces * 12;
        return { name, positions, indices };
    });
}

/**
 * Model mm → the viewer's display frame, in place, as _buildMeshFromPreview places the
 * terrain: X = col mm east, Y up = z, Z = row mm south (the model's y is north from the
 * last row). (x, y, z) -> (x, z, N - y) is a rotation (determinant +1), not a mirror, so the
 * triangle winding is kept: swapping it turned every part inside out, and with front-face
 * culling the viewer then drew each solid's inner faces.
 *
 * @param {{positions: Float32Array, indices: Uint32Array}} part
 * @param {{scale: number, xOffset: number, zOffset: number, rows: number, mmPerPx: number}} f
 */
export function toViewerFrame(part, f) {
    const p = part.positions;
    const northMm = (f.rows - 1) * f.mmPerPx;
    for (let i = 0; i < p.length; i += 3) {
        const x = p[i], y = p[i + 1], z = p[i + 2];
        p[i] = x * f.scale - f.xOffset;
        p[i + 1] = z * f.scale;
        p[i + 2] = (northMm - y) * f.scale - f.zOffset;
    }
    return part;
}

/** Display colour of a model part; buildings use the Edit map's building colour. */
export function partColor(name, buildingsColor = '#c8b89a') {
    return BUILDING_PARTS.has(name) ? buildingsColor : (PART_COLORS[name] || '#bbbbbb');
}

/** Parts the satellite image drapes over (their tops are horizontal, so a top-down image fits). */
const SATELLITE_PARTS = new Set(['terrain', 'roads', 'railways', 'trails', 'green']);
/** Colour of faces too steep for a top-down image (the terrain block's walls, cliffs). */
export const SOIL_COLOR = '#8a7258';
/** A face takes the image when its normal's vertical part is at least this (cos 60 deg). */
export const STEEP_FACE_MIN_NY = 0.5;

/**
 * Does `name` get the satellite image in the 3D view's Satellite colour mode? Ground,
 * roads, railways, trails and green always; buildings only with "Satellite image on
 * roofs"; water and the rest keep their own colour.
 */
export function takesSatelliteImage(name, roofs = false) {
    return SATELLITE_PARTS.has(name) || (!!roofs && BUILDING_PARTS.has(name));
}

/**
 * Planar top-down texture coordinates for a part already in the viewer frame
 * (toViewerFrame): u = east across the DEM box, v = 1 at the north edge, the same
 * mapping as the preview terrain (model-viewer.js::_buildMeshFromPreview), so the image
 * lands on the same place whichever mesh shows it.
 *
 * @param {Float32Array} positions viewer-frame xyz triples
 * @param {{scale: number, xOffset: number, zOffset: number, cols: number, rows: number, mmPerPx: number}} f
 * @returns {Float32Array} one (u, v) pair per vertex
 */
export function satelliteUVs(positions, f) {
    const w = Math.max((f.cols - 1) * f.mmPerPx * f.scale, 1e-9);
    const d = Math.max((f.rows - 1) * f.mmPerPx * f.scale, 1e-9);
    const n = positions.length / 3;
    const uv = new Float32Array(n * 2);
    for (let i = 0; i < n; i++) {
        uv[i * 2] = (positions[i * 3] + f.xOffset) / w;
        uv[i * 2 + 1] = 1 - (positions[i * 3 + 2] + f.zOffset) / d;
    }
    return uv;
}

/**
 * Split triangles by slope for the satellite drape: faces whose normal points up at
 * least `minNy` (default: flatter than 60 deg) take the image, every other face
 * (steep walls, undersides) gets a plain colour instead of a smeared image.
 * Positions are in the viewer frame (y up).
 *
 * @param {Float32Array} positions
 * @param {Uint32Array} indices
 * @returns {{flat: Uint32Array, steep: Uint32Array}} index lists, three per face
 */
export function splitFacesBySlope(positions, indices, minNy = STEEP_FACE_MIN_NY) {
    const flat = [], steep = [];
    for (let t = 0; t < indices.length; t += 3) {
        const a = indices[t] * 3, b = indices[t + 1] * 3, c = indices[t + 2] * 3;
        const ux = positions[b] - positions[a], uy = positions[b + 1] - positions[a + 1], uz = positions[b + 2] - positions[a + 2];
        const vx = positions[c] - positions[a], vy = positions[c + 1] - positions[a + 1], vz = positions[c + 2] - positions[a + 2];
        const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
        const len = Math.hypot(nx, ny, nz);
        const up = len > 0 && ny / len >= minNy;
        (up ? flat : steep).push(indices[t], indices[t + 1], indices[t + 2]);
    }
    return { flat: Uint32Array.from(flat), steep: Uint32Array.from(steep) };
}

/**
 * Parts of a shown finished model to hide after a City model layer edit, until the
 * rebuild lands: every part whose layer is now off, and the buildings when their
 * row changed at all (the quick view stands in for them meanwhile). Other changed
 * layers stay as built. Part names are the layer ids (ModelContainer.vue's rows);
 * the terrain is no layer and is never hidden.
 *
 * @param {Object<string, {enabled: boolean}>} built the layers the shown model was built with
 * @param {Object<string, {enabled: boolean}>} now   the layers the form holds now
 * @returns {Set<string>}
 */
export function staleParts(built, now) {
    const hide = new Set();
    for (const [id, style] of Object.entries(now || {})) {
        if (style?.enabled === false) hide.add(id);
    }
    if (now?.buildings && JSON.stringify(built?.buildings) !== JSON.stringify(now.buildings)) {
        hide.add('buildings');
    }
    return hide;
}
