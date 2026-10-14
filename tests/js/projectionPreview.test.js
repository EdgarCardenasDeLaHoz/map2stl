// Projection preview warp (modules/dem/projection-preview.js::warpPixels): the loaded layers
// re-drawn in a newly picked projection, in the server's predicted shape.
import { describe, it, expect } from 'vitest';
import {
    layerHasLanded, previewCanEnd, warpPixels,
} from '../../app/client/static/js/modules/dem/projection-preview.js';
import { projectedFrac } from '../../app/client/static/js/modules/dem/graticule.js';

const EUROPA = { north: 68.65655, south: 27.83908, east: 47.28516, west: -16.34766 };

/** An opaque image whose pixel (i, j) stores i in red/green and j in blue/alpha-free bytes. */
function codedImage(w, h) {
    const data = new Uint8ClampedArray(w * h * 4);
    for (let j = 0; j < h; j++) {
        for (let i = 0; i < w; i++) {
            const k = (j * w + i) * 4;
            data[k] = i & 255; data[k + 1] = i >> 8; data[k + 2] = j & 255; data[k + 3] = 255;
        }
    }
    return { data, width: w, height: h };
}
const px = (img, i, j) => {
    const k = (j * img.width + i) * 4;
    return { i: img.data[k] + (img.data[k + 1] << 8), j: img.data[k + 2], a: img.data[k + 3] };
};

describe('warpPixels', () => {
    it('is the identity when the projection does not change', () => {
        const src = codedImage(600, 578);
        const out = warpPixels(src, EUROPA, { projection: 'cosine' }, { projection: 'cosine' });
        expect([out.width, out.height]).toEqual([600, 578]);
        expect(Array.from(out.data)).toEqual(Array.from(src.data));
    });

    it('takes the server shape of the new projection', () => {
        const src = codedImage(600, 578);
        const sin = warpPixels(src, EUROPA, { projection: 'cosine' }, { projection: 'sinusoidal' });
        expect([sin.width, sin.height]).toEqual([600, 435]);
        const merc = warpPixels(src, EUROPA, { projection: 'cosine' }, { projection: 'mercator' });
        expect([merc.width, merc.height]).toEqual([573, 600]);
    });

    it('moves each place to where the new projection puts it', () => {
        const src = codedImage(600, 578);
        const out = warpPixels(src, EUROPA, { projection: 'cosine' }, { projection: 'mercator' });
        const from = projectedFrac(EUROPA, 'cosine');
        const to = projectedFrac(EUROPA, 'mercator');
        for (const [lat, lon] of [[41.9, 12.5], [59.3, 18.1], [30.0, 31.2]]) {
            const t = to(lat, lon), f = from(lat, lon);
            const got = px(out, Math.floor(t.x * out.width), Math.floor(t.y * out.height));
            expect(got.a).toBe(255);
            expect(Math.abs(got.i - f.x * 600)).toBeLessThan(2.5);
            expect(Math.abs(got.j - (Math.floor(f.y * 578) & 255))).toBeLessThan(3);
        }
    });

    it('leaves the corners a sinusoidal raster does not cover transparent', () => {
        const out = warpPixels(codedImage(600, 578), EUROPA, { projection: 'cosine' }, { projection: 'sinusoidal' });
        expect(px(out, 2, 2).a).toBe(0);                 // narrow north row: no data at the corner
        expect(px(out, 300, 217).a).toBe(255);           // the middle is covered
    });

    it('warps a 600 px layer fast enough to feel instant', () => {
        const src = codedImage(600, 578);
        warpPixels(src, EUROPA, { projection: 'cosine' }, { projection: 'sinusoidal' });   // warm up
        const t0 = performance.now();
        warpPixels(src, EUROPA, { projection: 'cosine' }, { projection: 'sinusoidal' });
        expect(performance.now() - t0).toBeLessThan(250);
    });
});

// Hand-over from preview to real layers (layerHasLanded, previewCanEnd). The live bug: the
// reload ended the preview as soon as the DEM landed, while Rivers & lakes was still fetching,
// so that layer had no source and the stack showed its stale preview buffer misplaced.
describe('projection preview hand-over', () => {
    const canvas = (pending) => ({ dataset: pending ? { pending: '1' } : {} });

    it('holds every layer until the DEM for the new pick has landed', () => {
        const fresh = canvas();
        expect(layerHasLanded('Dem', fresh, null, false)).toBe(false);
        expect(layerHasLanded('Borders', fresh, null, false)).toBe(false);
    });

    it('holds the DEM until the render worker has painted it', () => {
        expect(layerHasLanded('Dem', canvas(true), null, true)).toBe(false);
        expect(layerHasLanded('Dem', canvas(false), null, true)).toBe(true);
        expect(layerHasLanded('Dem', null, null, true)).toBe(false);
    });

    it('holds other layers until a canvas other than the one seen at the DEM landing appears', () => {
        const old = canvas();
        expect(layerHasLanded('WaterHydrology', null, null, true)).toBe(false);   // still fetching
        expect(layerHasLanded('Borders', old, old, true)).toBe(false);            // old projection
        expect(layerHasLanded('Borders', canvas(), old, true)).toBe(true);
    });

    it('does not end at release while Rivers & lakes is still loading', () => {
        const dem = canvas();
        const st = {
            released: true, deadline: 1000, demLanded: true,
            seen: new Map([['Dem', null], ['WaterHydrology', null]]),
        };
        expect(previewCanEnd(st, { Dem: dem, WaterHydrology: null }, 10)).toBe(false);
        expect(previewCanEnd(st, { Dem: dem, WaterHydrology: canvas() }, 10)).toBe(true);
        // switched off while held: no longer waited for
        expect(previewCanEnd(st, { Dem: dem }, 10)).toBe(true);
        // a fetch that never lands: the hold times out
        expect(previewCanEnd(st, { Dem: dem, WaterHydrology: null }, 1000)).toBe(true);
    });

    it('never ends before the reload releases it, and never waits for layers nothing reloads', () => {
        const mesh = canvas();
        const st = {
            released: false, deadline: Infinity, demLanded: true,
            seen: new Map([['Dem', null], ['MeshImport', mesh]]),
        };
        const sources = { Dem: canvas(), MeshImport: mesh };
        expect(previewCanEnd(st, sources, 10)).toBe(false);
        st.released = true;
        expect(previewCanEnd(st, sources, 10)).toBe(true);
    });
});
