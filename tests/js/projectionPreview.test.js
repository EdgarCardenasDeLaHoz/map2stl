// Projection preview warp (modules/dem/projection-preview.js::warpPixels): the loaded layers
// re-drawn in a newly picked projection, in the server's predicted shape.
import { describe, it, expect, afterEach, vi } from 'vitest';
import {
    endProjectionPreview, isProjectionPreviewActive, layerHasLanded, layerSettled,
    markPreviewDemLanded, previewCanEnd, previewSize, releaseProjectionPreview, releaseSettles,
    settleLayer, settlePreviewLayer, settleProjectionPreview, startProjectionPreview, unsettledModes,
    warpPixels,
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

// Hand-over from preview to real layers (layerHasLanded, layerSettled, settleLayer,
// previewCanEnd). Two live bugs: the reload ended the preview as soon as the DEM landed, while
// Rivers & lakes was still fetching (a misplaced stale buffer); then the preview waited for a
// new Rivers & lakes canvas that never came, because Everest has no rivers (pill held 31 s).
describe('projection preview hand-over', () => {
    const canvas = (pending) => ({ dataset: pending ? { pending: '1' } : {} });
    /** A preview state holding `modes`, for pick 7. */
    const state = (modes, extra = {}) => ({
        pick: 7, released: false, deadline: Infinity, demLanded: false,
        settled: new Set(), base: new Map(modes.map((m) => [m, {}])), ...extra,
    });

    it('shows a layer for real once a new canvas appears (display fallback)', () => {
        const old = canvas();
        expect(layerHasLanded('Borders', canvas(), null, false)).toBe(false);   // DEM not in yet
        expect(layerHasLanded('WaterHydrology', null, null, true)).toBe(false);
        expect(layerHasLanded('Borders', old, old, true)).toBe(false);            // old projection
        expect(layerHasLanded('Borders', canvas(), old, true)).toBe(true);
    });

    it('holds the DEM until it has landed and the render worker has painted it', () => {
        const st = state(['Dem']);
        expect(layerSettled('Dem', canvas(), st)).toBe(false);
        st.demLanded = true;
        expect(layerSettled('Dem', canvas(true), st)).toBe(false);
        expect(layerSettled('Dem', canvas(false), st)).toBe(true);
        expect(layerHasLanded('Dem', canvas(true), null, true)).toBe(false);
        expect(layerHasLanded('Dem', canvas(false), null, true)).toBe(true);
    });

    it('does not end at release while Rivers & lakes is still loading', () => {
        const st = state(['Dem', 'WaterHydrology'], { demLanded: true });
        const dem = canvas();
        releaseLike(st, ['WaterHydrology']);
        expect(previewCanEnd(st, { Dem: dem, WaterHydrology: null }, 10)).toBe(false);
        settleLayer(st, 'WaterHydrology', 7);
        expect(previewCanEnd(st, { Dem: dem, WaterHydrology: canvas() }, 10)).toBe(true);
    });

    it('ends when an empty layer settles: no rivers in the box, null before and after', () => {
        const st = state(['Dem', 'WaterHydrology', 'Borders'], { demLanded: true });
        const sources = { Dem: canvas(), WaterHydrology: null, Borders: canvas() };
        releaseLike(st, ['WaterHydrology']);
        expect(previewCanEnd(st, sources, 10)).toBe(false);
        expect(settleLayer(st, 'WaterHydrology', 7)).toBe(true);
        expect(previewCanEnd(st, sources, 10)).toBe(true);
    });

    it('ends when a layer whose fetch failed settles', () => {
        const st = state(['Dem', 'Borders'], { demLanded: true });
        const old = canvas();
        releaseLike(st, ['Borders']);
        // The failed fetch leaves the old projection's canvas in place.
        expect(previewCanEnd(st, { Dem: canvas(), Borders: old }, 10)).toBe(false);
        settleLayer(st, 'Borders', 7);
        expect(previewCanEnd(st, { Dem: canvas(), Borders: old }, 10)).toBe(true);
    });

    it('ends when a layer comes back as the same cached canvas object', () => {
        const st = state(['Dem', 'Trails'], { demLanded: true });
        const cached = canvas();
        releaseLike(st, ['Trails']);
        settleLayer(st, 'Trails', 7);
        expect(layerSettled('Trails', cached, st)).toBe(true);
        expect(previewCanEnd(st, { Dem: canvas(), Trails: cached }, 10)).toBe(true);
    });

    it('ignores a settle from an older pick', () => {
        const st = state(['Dem', 'Borders'], { demLanded: true });
        releaseLike(st, ['Borders']);
        expect(settleLayer(st, 'Borders', 6)).toBe(false);
        expect(previewCanEnd(st, { Dem: canvas(), Borders: canvas() }, 10)).toBe(false);
        expect(settleLayer(null, 'Borders', 7)).toBe(false);   // no preview: nothing to settle
    });

    it('stops waiting for a layer switched off, and gives up at the hold deadline', () => {
        const st = state(['Dem', 'WaterHydrology'], { demLanded: true });
        releaseLike(st, ['WaterHydrology']);
        expect(previewCanEnd(st, { Dem: canvas() }, 10)).toBe(true);
        expect(previewCanEnd(st, { Dem: canvas(), WaterHydrology: null }, 1000)).toBe(true);
    });

    it('never ends before the reload releases it, and never waits for layers nothing reloads', () => {
        const mesh = canvas();
        const st = state(['Dem', 'MeshImport', 'CompositeDem'], { demLanded: true });
        const sources = { Dem: canvas(), MeshImport: mesh, CompositeDem: null };
        expect(previewCanEnd(st, sources, 10)).toBe(false);
        st.released = true;
        st.deadline = 1000;
        expect(previewCanEnd(st, sources, 10)).toBe(true);
    });

    it('release settles every held layer but the DEM, pending ones and ones nothing reloads', () => {
        const st = state(['Dem', 'Borders', 'Trails', 'CompositeDem', 'MeshImport']);
        releaseSettles(st, ['Trails']);
        expect([...st.settled].sort()).toEqual(['Borders']);
        // The composite keeps its preview: settling it would show the canvas the reload nulled.
        expect(layerSettled('CompositeDem', null, st)).toBe(false);
    });
});

/** What releaseProjectionPreview does to the state. */
function releaseLike(st, pending) {
    releaseSettles(st, pending);
    st.released = true;
    st.deadline = 1000;
}

describe('previewSize', () => {
    it('scales the longest side down to the cap and keeps the aspect', () => {
        expect(previewSize(1000, 578, 600)).toEqual({ width: 600, height: 347 });
        expect(previewSize(425, 600, 600)).toEqual({ width: 425, height: 600 });
        expect(previewSize(300, 200, 600)).toEqual({ width: 300, height: 200 });   // never up
        expect(previewSize(4000, 1, 600)).toEqual({ width: 600, height: 1 });      // never 0
    });
});

// The Colombia stall (cosine to mercator, 2026-10-14): water landed before the DEM, the composite
// before Borders and Rivers & lakes, and the release came before those two settled. The pure
// state was right; the end check ran only on the stack redraw, a requestAnimationFrame that a
// hidden pane never fires, so the pill stayed for the 30 s hold.
describe('projection preview: layers that settle after the release', () => {
    it('names what it still waits for, in the stalled order of events', () => {
        const st = {
            pick: 3, released: false, deadline: Infinity, demLanded: false, settled: new Set(),
            base: new Map([['Dem', {}], ['WaterHydrology', {}], ['Borders', {}], ['CompositeDem', {}]]),
        };
        const sources = { Dem: { dataset: {} }, WaterHydrology: null, Borders: null, CompositeDem: null };
        // 104 ms: water mask (no stack mode) lands; 615: DEM lands; 2964: Sat (not held) settles
        st.demLanded = true;
        settleLayer(st, 'Sat', 3);
        // 2967: the reload releases with its own layers still running
        releaseSettles(st, ['Borders', 'WaterHydrology', 'Sat']);
        st.released = true;
        st.deadline = 30000;
        expect(unsettledModes(st, sources)).toEqual(['WaterHydrology', 'Borders']);
        // 2990: the composite lands (nothing reloads it: never waited for)
        sources.CompositeDem = { dataset: {} };
        expect(previewCanEnd(st, sources, 2990)).toBe(false);
        // 3629 / 3658: Rivers & lakes, then Borders
        settleLayer(st, 'WaterHydrology', 3);
        expect(unsettledModes(st, sources)).toEqual(['Borders']);
        settleLayer(st, 'Borders', 3);
        expect(unsettledModes(st, sources)).toEqual([]);
        expect(previewCanEnd(st, sources, 3658)).toBe(true);
    });
});

describe('projection preview module: ends without a stack redraw', () => {
    // A node stand-in for the few canvas calls the snapshot and warp make.
    const fakeCanvas = (w = 40, h = 30) => ({
        width: w, height: h, dataset: {},
        getContext: () => ({
            drawImage() {}, putImageData() {},
            getImageData: (x, y, cw, ch) => ({ data: new Uint8ClampedArray(cw * ch * 4), width: cw, height: ch }),
        }),
    });
    const BOX = { north: 12, south: -4, east: -67, west: -79 };

    afterEach(() => {
        endProjectionPreview();
        vi.unstubAllGlobals();
    });

    it('ends when the last held layer settles after the release, with no redraw', () => {
        vi.stubGlobal('document', { createElement: () => fakeCanvas(), getElementById: () => null });
        vi.stubGlobal('ImageData', class { constructor(d, w, h) { Object.assign(this, { data: d, width: w, height: h }); } });
        vi.stubGlobal('window', {});
        const real = { Dem: fakeCanvas(), WaterHydrology: fakeCanvas(), Borders: fakeCanvas() };
        expect(startProjectionPreview({
            pick: 5, bbox: BOX, from: { projection: 'cosine' }, to: { projection: 'mercator' }, dim: 40, sources: real,
        })).toBe(true);
        const now = { Dem: fakeCanvas(), WaterHydrology: null, Borders: null };
        markPreviewDemLanded(now);
        releaseProjectionPreview(now, ['WaterHydrology', 'Borders']);
        expect(isProjectionPreviewActive()).toBe(true);
        expect(settlePreviewLayer('WaterHydrology', 5, now)).toBe(true);
        expect(isProjectionPreviewActive()).toBe(true);
        expect(settlePreviewLayer('Borders', 4, now)).toBe(false);   // an older pick's Borders
        expect(isProjectionPreviewActive()).toBe(true);
        expect(settlePreviewLayer('Borders', 5, now)).toBe(true);
        expect(isProjectionPreviewActive()).toBe(false);
    });

    // Colorado / Norway, water and land cover at t=0 (2026-10-14): both land before the DEM, so
    // the release follows the DEM's arrival at once, before the render worker paints it. The
    // DEM then settles on the worker's answer (dem-main.js::_onDemWorkerMessage), after the
    // release, and the only check left was the rAF stack redraw.
    it('keeps early settles through the DEM landing, and ends on the DEM paint after the release', () => {
        vi.stubGlobal('document', { createElement: () => fakeCanvas(), getElementById: () => null });
        vi.stubGlobal('ImageData', class { constructor(d, w, h) { Object.assign(this, { data: d, width: w, height: h }); } });
        vi.stubGlobal('window', {});
        const real = { Dem: fakeCanvas(), WaterHydrology: fakeCanvas(), Sat: fakeCanvas() };
        startProjectionPreview({
            pick: 9, bbox: BOX, from: { projection: 'mercator' }, to: { projection: 'sinusoidal' }, dim: 40, sources: real,
        });
        const now = { Dem: null, WaterHydrology: null, Sat: real.Sat };
        // Land cover and Rivers & lakes settle before the DEM lands
        settlePreviewLayer('Sat', 9, now);
        settlePreviewLayer('WaterHydrology', 9, now);
        // The DEM lands: a new canvas, blank until the worker paints it
        now.Dem = fakeCanvas();
        now.Dem.dataset.pending = '1';
        markPreviewDemLanded(now);
        releaseProjectionPreview(now, ['Sat', 'WaterHydrology']);
        expect(isProjectionPreviewActive()).toBe(true);
        // The worker paints: dem-main.js deletes the flag and checks at once
        delete now.Dem.dataset.pending;
        settleProjectionPreview(now);
        expect(isProjectionPreviewActive()).toBe(false);
    });
});
