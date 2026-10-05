import { describe, it, expect, beforeAll } from 'vitest';

// LAYER_CANVAS_IDS has no CityOverlay entry on purpose: the city overlay is the
// `.osm-overlay` element, not a canvas buffer. clearAllLayerBuffers walks every
// layer, and getOrCreateCanvas used to append a stray id-less canvas for it.

let appended;

beforeAll(async () => {
    appended = [];
    const makeCanvas = () => ({ width: 0, height: 0, className: '', id: '',
        style: {}, classList: { add() {}, remove() {}, toggle() {} },
        getContext: () => ({ clearRect() {}, drawImage() {}, setTransform() {} }) });
    const stack = { appendChild: (c) => { appended.push(c); return c; }, querySelector: () => null };
    globalThis.document = {
        readyState: 'complete',
        createElement: () => makeCanvas(),
        getElementById: (id) => (id === 'layersStack' ? stack : null),
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
    };
    globalThis.window = globalThis.window || {};
    Object.assign(globalThis.window, {
        appState: {}, EV: {}, events: { on() {}, emit() {} }, addEventListener() {},
    });
    globalThis.requestAnimationFrame = (f) => f();
    await import('../../app/client/static/js/modules/layers/stacked-layers.js');
});

describe('clearAllLayerBuffers', () => {
    it('creates canvases only for canvas layers, never for CityOverlay', () => {
        window.clearAllLayerBuffers();
        window.clearAllLayerBuffers();
        expect(appended.every((c) => c.id && c.id.startsWith('layer'))).toBe(true);
        expect(appended.map((c) => c.id)).not.toContain('');
        expect(new Set(appended.map((c) => c.id)).size).toBe(appended.length);   // once each
    });
});
