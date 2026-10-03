/**
 * city-fullmodel.js — the finished City Model's parts in the Extrude 3D view.
 *
 * After the quick view (city-quickview.js) the viewer starts the same City Model build
 * the Download button runs and, when it is done, replaces the quick-view buildings with
 * the model's own parts (buildings, roads, water, green, …) as they will print. The
 * terrain stays the preview's: it comes from the same terrain stage.
 *
 * Pure: parsing and coordinate mapping only (vitest imports it).
 *
 * File layout (numpy2stl.io.write_parts_file): uint32 header
 * length, UTF-8 JSON header {parts: [{name, vertices, faces}]} padded to 4 bytes, then
 * per part float32 vertices (model mm: x east, y north, z up) and uint32 faces.
 */

/** Parts with their own colour; anything else is drawn light grey. */
const PART_COLORS = {
    roads: '#cc8844', waterways: '#4488cc', green: '#6aa84f', railways: '#666666',
    trails: '#b5651d',
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
 * last row). The y flip mirrors the mesh, so the triangle winding is reversed too.
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
    const t = part.indices;
    for (let i = 0; i < t.length; i += 3) { const b = t[i + 1]; t[i + 1] = t[i + 2]; t[i + 2] = b; }
    return part;
}

/** Display colour of a model part; buildings use the Edit map's building colour. */
export function partColor(name, buildingsColor = '#c8b89a') {
    return BUILDING_PARTS.has(name) ? buildingsColor : (PART_COLORS[name] || '#bbbbbb');
}
