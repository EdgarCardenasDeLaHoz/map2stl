/**
 * cityFullmodel.test.js — the finished City Model's parts in the Extrude viewer
 * (app/client/static/js/modules/export/city-fullmodel.js). The file layout matches
 * numpy2stl.io.write_parts_file (tests/test_model_parts.py).
 */
import { describe, it, expect } from 'vitest';
import { parseModelParts, partColor, staleParts, toViewerFrame } from '../../app/client/static/js/modules/export/city-fullmodel.js';

function partsFile(parts) {
    let header = new TextEncoder().encode(JSON.stringify({
        parts: parts.map(p => ({ name: p.name, vertices: p.v.length / 3, faces: p.f.length / 3 })),
    }));
    const pad = (4 - (header.length % 4)) % 4;
    header = new Uint8Array([...header, ...new Array(pad).fill(32)]);
    const size = 4 + header.length + parts.reduce((n, p) => n + p.v.length * 4 + p.f.length * 4, 0);
    const buf = new ArrayBuffer(size);
    new DataView(buf).setUint32(0, header.length, true);
    new Uint8Array(buf, 4, header.length).set(header);
    let off = 4 + header.length;
    for (const p of parts) {
        new Float32Array(buf, off, p.v.length).set(p.v); off += p.v.length * 4;
        new Uint32Array(buf, off, p.f.length).set(p.f); off += p.f.length * 4;
    }
    return buf;
}

describe('parseModelParts', () => {
    it('reads every part of the file', () => {
        const buf = partsFile([
            { name: 'terrain', v: [0, 0, 1, 1, 0, 1, 0, 1, 1], f: [0, 1, 2] },
            { name: 'buildings', v: [2, 2, 5, 3, 2, 5, 2, 3, 5, 3, 3, 5], f: [0, 1, 2, 1, 3, 2] },
        ]);
        const parts = parseModelParts(buf);
        expect(parts.map(p => p.name)).toEqual(['terrain', 'buildings']);
        expect([...parts[1].positions.slice(0, 3)]).toEqual([2, 2, 5]);
        expect([...parts[1].indices]).toEqual([0, 1, 2, 1, 3, 2]);
    });
});

describe('toViewerFrame', () => {
    it('puts model mm where the preview puts the terrain, north away from the viewer', () => {
        // rows 11 at 2 mm/px: the model's north edge is y = 20 mm, i.e. display row 0.
        const part = { positions: new Float32Array([4, 20, 3, 4, 0, 3, 6, 0, 3]), indices: new Uint32Array([0, 1, 2]) };
        toViewerFrame(part, { scale: 0.5, xOffset: 1, zOffset: 2, rows: 11, mmPerPx: 2 });
        expect([...part.positions.slice(0, 3)]).toEqual([4 * 0.5 - 1, 3 * 0.5, 0 * 0.5 - 2]);
        expect(part.positions[5]).toBe(20 * 0.5 - 2);                 // south edge
        expect([...part.indices]).toEqual([0, 1, 2]);                  // a rotation: winding kept
    });

    it('keeps a closed solid outward-facing (positive signed volume)', () => {
        // unit cube, outward counter-clockwise triangles in model mm
        const v = [0,0,0, 1,0,0, 1,1,0, 0,1,0, 0,0,1, 1,0,1, 1,1,1, 0,1,1];
        const f = [0,2,1, 0,3,2, 4,5,6, 4,6,7, 0,1,5, 0,5,4, 1,2,6, 1,6,5, 2,3,7, 2,7,6, 3,0,4, 3,4,7];
        const vol = ({ positions: p, indices: t }) => {
            let s = 0;
            for (let i = 0; i < t.length; i += 3) {
                const [a, b, c] = [t[i] * 3, t[i + 1] * 3, t[i + 2] * 3];
                s += p[a] * (p[b + 1] * p[c + 2] - p[b + 2] * p[c + 1])
                   - p[a + 1] * (p[b] * p[c + 2] - p[b + 2] * p[c])
                   + p[a + 2] * (p[b] * p[c + 1] - p[b + 1] * p[c]);
            }
            return s / 6;
        };
        const part = { positions: new Float32Array(v), indices: new Uint32Array(f) };
        expect(vol(part)).toBeCloseTo(1);
        toViewerFrame(part, { scale: 1, xOffset: 0, zOffset: 0, rows: 3, mmPerPx: 1 });
        expect(vol(part)).toBeCloseTo(1);
    });
});

describe('partColor', () => {
    it('gives buildings the Edit map colour and other parts their own', () => {
        expect(partColor('buildings', '#123456')).toBe('#123456');
        expect(partColor('churches', '#123456')).toBe('#123456');
        expect(partColor('roads')).toBe('#b08b6a');
        expect(partColor('something new')).toBe('#bbbbbb');
    });
});

describe('staleParts', () => {
    const built = {
        buildings: { enabled: true, mode: 'extrude', height_scale: 1 },
        roads: { enabled: true, mode: 'raised', offset_mm: 0.4 },
    };

    it('hides nothing when the layers are as built', () => {
        expect(staleParts(built, structuredClone(built)).size).toBe(0);
    });

    it('hides the parts of layers turned off', () => {
        const now = structuredClone(built);
        now.roads.enabled = false;
        expect([...staleParts(built, now)]).toEqual(['roads']);
    });

    it('keeps a changed surface layer until the rebuild', () => {
        const now = structuredClone(built);
        now.roads.offset_mm = 1;
        expect(staleParts(built, now).size).toBe(0);
    });

    it('hides changed buildings, which the quick view stands in for', () => {
        const now = structuredClone(built);
        now.buildings.height_scale = 2;
        expect([...staleParts(built, now)]).toEqual(['buildings']);
    });

    it('never hides the terrain or parts without a layer row', () => {
        expect(staleParts(built, {}).size).toBe(0);
        expect(staleParts(undefined, undefined).size).toBe(0);
    });
});
