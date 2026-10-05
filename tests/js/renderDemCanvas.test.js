import { describe, it, expect, beforeAll, beforeEach } from 'vitest';

// renderDEMCanvas used to re-announce the DEM (DEM_LOADED, model rebuild, a fresh
// lastDemData without bbox/sourceDimensions) on every recolour and resize frame
// (audit 2026-10-05). Only a new DEM - values, size or display range - is announced now.

let announced;
let rebuilds;

beforeAll(async () => {
    const ctx = { putImageData() {} };
    globalThis.document = {
        createElement: () => ({ getContext: () => ctx, classList: { add() {} }, style: {} }),
        getElementById: () => null,
        querySelector: () => null,
        addEventListener() {},
    };
    globalThis.ImageData = class { constructor(w, h) { this.data = new Uint8ClampedArray(w * h * 4); } };
    globalThis.window = globalThis.window || {};
    Object.assign(globalThis.window, {
        appState: { layerBboxes: {} },
        EV: { DEM_LOADED: 'dem:loaded' },
        events: { emit: (ev) => { if (ev === 'dem:loaded') announced++; }, on() {} },
        buildColorLUT: () => new Uint8Array(1024 * 3),
        setLayerStatus() {},
        _modelViewerAutoRebuild: () => { rebuilds++; },
        addEventListener() {},
    });
    await import('../../app/client/static/js/modules/dem/dem-main.js');
});

beforeEach(() => { announced = 0; rebuilds = 0; });

describe('renderDEMCanvas', () => {
    it('announces a new DEM once, not again for a recolour or a resize frame', () => {
        const values = new Float32Array([1, 2, 3, 4]);
        window.renderDEMCanvas(values, 2, 2, 'terrain', 1, 4);
        window.appState.lastDemData.bbox = { north: 1, south: 0, east: 1, west: 0 };
        window.appState.lastDemData.sourceDimensions = [2, 2];
        expect(announced).toBe(1);
        expect(rebuilds).toBe(1);

        window.renderDEMCanvas(values, 2, 2, 'viridis', 1, 4);   // recolour
        window.renderDEMCanvas(values, 2, 2, 'viridis', 1, 4);   // resize frame
        expect(announced).toBe(1);
        expect(rebuilds).toBe(1);
        expect(window.appState.lastDemData.colormap).toBe('viridis');
        expect(window.appState.lastDemData.bbox).toEqual({ north: 1, south: 0, east: 1, west: 0 });
    });

    it('announces a changed display range (curve editor re-normalises)', () => {
        const values = new Float32Array([1, 2, 3, 4]);
        window.renderDEMCanvas(values, 2, 2, 'terrain', 1, 4);
        announced = 0;
        window.renderDEMCanvas(values, 2, 2, 'terrain', 0, 10);
        expect(announced).toBe(1);
    });

    it('announces new values on the same grid and keeps bbox and source size', () => {
        const values = new Float32Array([1, 2, 3, 4]);
        window.renderDEMCanvas(values, 2, 2, 'terrain', 1, 4);
        window.appState.lastDemData.bbox = { north: 5, south: 4, east: 5, west: 4 };
        window.appState.lastDemData.sourceDimensions = [3, 3];
        announced = 0;
        const curved = new Float32Array([2, 3, 4, 5]);                // a curve apply
        window.renderDEMCanvas(curved, 2, 2, 'terrain', 1, 4);
        expect(announced).toBe(1);
        expect(window.appState.lastDemData.values).toBe(curved);
        expect(window.appState.lastDemData.bbox).toEqual({ north: 5, south: 4, east: 5, west: 4 });
        expect(window.appState.lastDemData.sourceDimensions).toEqual([3, 3]);
    });

    it('drops bbox and source size when the grid size changes (a new region)', () => {
        window.renderDEMCanvas(new Float32Array(4), 2, 2, 'terrain', 0, 1);
        window.appState.lastDemData.bbox = { north: 1, south: 0, east: 1, west: 0 };
        window.renderDEMCanvas(new Float32Array(9), 3, 3, 'terrain', 0, 1);
        expect(window.appState.lastDemData.bbox).toBeUndefined();
    });

    it('skipStateUpdate renders without touching state', () => {
        const before = window.appState.lastDemData;
        window.renderDEMCanvas(new Float32Array(4), 2, 2, 'terrain', 0, 1, { skipStateUpdate: true });
        expect(window.appState.lastDemData).toBe(before);
        expect(announced).toBe(0);
    });
});
