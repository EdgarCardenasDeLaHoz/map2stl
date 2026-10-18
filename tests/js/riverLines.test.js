import { describe, it, expect } from 'vitest';
import {
    decodeRiverLines, projectLines, visibleFrac, fracInside, fracToBbox, riverLinesQuery,
    strokeRiverLines,
} from '../../app/client/static/js/modules/layers/river-lines.js';

const b64 = (typed) => Buffer.from(typed.buffer, typed.byteOffset, typed.byteLength).toString('base64');
const BBOX = { north: 10, south: 0, east: 20, west: 10 };

function payload() {
    // Two lines: (10,10)-(20,0) order 3; (15,5)-(15,7)-(16,7) order 6. lon, lat pairs.
    return {
        coords_b64: b64(new Float32Array([10, 10, 20, 0, 15, 5, 15, 7, 16, 7])),
        offsets_b64: b64(new Uint32Array([0, 2, 5])),
        orders_b64: b64(new Uint8Array([3, 6])),
    };
}

describe('decodeRiverLines', () => {
    it('round-trips the server encoding', () => {
        const l = decodeRiverLines(payload());
        expect(l.count).toBe(2);
        expect([...l.offsets]).toEqual([0, 2, 5]);
        expect([...l.orders]).toEqual([3, 6]);
        expect(l.coords[8]).toBe(16);
    });
    it('is empty for an empty payload', () => {
        expect(decodeRiverLines({}).count).toBe(0);
    });
});

describe('projectLines', () => {
    it('maps lon/lat to raster fractions (north-west = 0, 0)', () => {
        const f = projectLines(decodeRiverLines(payload()), BBOX);
        expect([...f.slice(0, 4)]).toEqual([0, 0, 1, 1]);
        expect(f[4]).toBeCloseTo(0.5); expect(f[5]).toBeCloseTo(0.5);
    });
});

describe('visibleFrac', () => {
    const layout = { x: 100, y: 0, w: 800, h: 600 };
    it('is the whole raster at zoom 1', () => {
        expect(visibleFrac(layout, { scale: 1, offsetX: 0, offsetY: 0 }, 1000, 600))
            .toEqual({ x0: 0, y0: 0, x1: 1, y1: 1 });
    });
    it('is the quarter on screen at zoom 2 from the corner', () => {
        const r = visibleFrac(layout, { scale: 2, offsetX: -200, offsetY: 0 }, 800, 600);
        expect(r.x0).toBeCloseTo(0); expect(r.x1).toBeCloseTo(0.5);
        expect(r.y0).toBeCloseTo(0); expect(r.y1).toBeCloseTo(0.5);
    });
    it('pads and clamps', () => {
        const r = visibleFrac(layout, { scale: 4, offsetX: -1800, offsetY: -1200 }, 1000, 600, 0.25);
        expect(r.x0).toBeLessThan(0.5); expect(r.x1).toBeLessThanOrEqual(1);
    });
    it('is null with no layout or nothing on screen', () => {
        expect(visibleFrac(null, { scale: 1, offsetX: 0, offsetY: 0 }, 10, 10)).toBeNull();
        expect(visibleFrac(layout, { scale: 1, offsetX: 5000, offsetY: 0 }, 1000, 600)).toBeNull();
    });
});

describe('fracInside / fracToBbox / riverLinesQuery', () => {
    it('tests containment', () => {
        expect(fracInside({ x0: 0.2, y0: 0.2, x1: 0.4, y1: 0.4 }, { x0: 0, y0: 0, x1: 0.5, y1: 0.5 })).toBe(true);
        expect(fracInside({ x0: 0.2, y0: 0.2, x1: 0.6, y1: 0.4 }, { x0: 0, y0: 0, x1: 0.5, y1: 0.5 })).toBe(false);
    });
    it('turns the north-west quarter into its lat/lon box, clamped to the region', () => {
        expect(fracToBbox({ x0: -0.1, y0: 0, x1: 0.5, y1: 0.5 }, BBOX))
            .toEqual({ north: 10, south: 5, east: 15, west: 10 });
    });
    it('builds the query, view optional', () => {
        const q = riverLinesQuery({ bbox: BBOX, view: { north: 9, south: 5, east: 15, west: 11 },
            widthPx: 512.4, heightPx: 3, minOrder: 3 });
        expect(q.get('min_order')).toBe('3');
        expect(q.get('width_px')).toBe('512');
        expect(q.get('height_px')).toBe('16');
        expect(q.get('view_west')).toBe('11.000000');
        expect(riverLinesQuery({ bbox: BBOX, view: null, widthPx: 100, heightPx: 100, minOrder: 1 })
            .has('view_north')).toBe(false);
    });
});

describe('strokeRiverLines', () => {
    it('draws an edge pass then a fill pass, small orders first', () => {
        const calls = [];
        const ctx = {
            set lineWidth(v) { this._w = v; }, set strokeStyle(v) { this._s = v; },
            beginPath() {}, moveTo() {}, lineTo() {},
            stroke() { calls.push([this._w, this._s]); },
        };
        const l = decodeRiverLines(payload());
        const n = strokeRiverLines(ctx, l, projectLines(l, BBOX), (x, y) => [x * 100, y * 100]);
        expect(n).toBe(2);
        expect(calls).toHaveLength(4);                       // 2 orders x (edge, fill)
        expect(calls[0][1]).toMatch(/^rgba\(0, 35, 70/);      // edge first
        expect(calls[2][1]).toBe('rgb(110, 230, 255)');      // then cyan
        expect(calls[0][0]).toBeGreaterThan(calls[2][0]);    // edge wider than fill
        expect(calls[3][0]).toBeGreaterThan(calls[2][0]);    // order 6 wider than order 3
    });
});
