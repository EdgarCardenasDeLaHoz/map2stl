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
