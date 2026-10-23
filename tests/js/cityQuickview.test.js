/**
 * cityQuickview.test.js — buildings on the Extrude preview
 * (app/client/static/js/modules/export/city-quickview.js).
 */
import { describe, it, expect } from 'vitest';
import { buildingPrisms, groundRange, WALL_SINK_MM, demGroundMm, roofHeightM, roofKind, MIN_HEIGHT_MM } from '../../app/client/static/js/modules/export/city-quickview.js';

// A square 2 x 2 px footprint; lon → x, lat → y directly.
const square = (h) => ({
    properties: h == null ? {} : { height_m: h },
    geometry: { type: 'Polygon', coordinates: [[[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]] },
});
const fan = (contour) => contour.slice(2).map((_, i) => [0, i + 1, i + 2]);
const opts = (ground) => ({
    toPx: (lat, lon) => ({ x: lon, y: lat }), groundMm: ground, zMmPerM: 0.1, mmPerPx: 2,
    triangulate: fan,
});
const ys = (g) => [...g.positions].filter((_, i) => i % 3 === 1);

describe('buildingPrisms', () => {
    it('stands each building on the highest ground under it, by the City Model rule', () => {
        const g = buildingPrisms([square(30)], opts((x) => x));   // ground rises with x: 1..3 mm
        expect(g.count).toBe(1);
        expect(Math.max(...ys(g))).toBeCloseTo(3 + 30 * 0.1);     // top = highest ground + 3 mm
        expect(Math.min(...ys(g))).toBeCloseTo(1 - WALL_SINK_MM);   // walls reach the lowest ground, less the sink
        const xs = [...g.positions].filter((_, i) => i % 3 === 0);
        expect(Math.max(...xs)).toBe(6);                          // px × mm per px
    });

    it('never prints thinner than MIN_HEIGHT_MM, and a missing height is 10 m', () => {
        const thin = buildingPrisms([square(1)], opts(() => 0));
        expect(Math.max(...ys(thin))).toBeCloseTo(MIN_HEIGHT_MM);
        const none = buildingPrisms([square(null)], opts(() => 0));
        expect(Math.max(...ys(none))).toBeCloseTo(1.0);
    });

    it('builds a roof and four walls; skips non-polygons', () => {
        const g = buildingPrisms([square(30), { geometry: { type: 'LineString', coordinates: [] } }], opts(() => 0));
        expect(g.count).toBe(1);
        expect(g.indices.length).toBe(2 * 3 + 4 * 6);
    });
});

describe('demGroundMm', () => {
    it('maps the DEM grid onto the preview grid with the preview scale', () => {
        const dem = { width: 2, height: 1, values: [100, 300] };
        const z = demGroundMm(dem, 4, 2, { z_mm_per_m: 0.01, elev_min_m: 100, base_mm: 5 });
        expect(z(0, 0)).toBeCloseTo(5);
        expect(z(3, 1)).toBeCloseTo(7);
    });
});

describe('pitched roofs', () => {
    // 4 x 2 px footprint (long axis x), 20 m tall at 0.1 mm/m: top = 2 mm above flat ground.
    const house = (props) => ({
        properties: { height_m: 20, ...props },
        geometry: { type: 'Polygon', coordinates: [[[0, 0], [4, 0], [4, 2], [0, 2], [0, 0]]] },
    });
    const flat = () => opts(() => 0);
    const maxY = (g) => Math.max(...ys(g));

    it('maps roof:shape to a kind and defaults the roof height to 30 %, capped at 50 %', () => {
        expect(roofKind({ 'roof:shape': 'Gabled' })).toBe('gable');
        expect(roofKind({ 'roof:shape': 'pyramidal' })).toBe('hip');
        expect(roofKind({ 'roof:shape': 'flat' })).toBeNull();
        expect(roofKind({})).toBeNull();
        expect(roofHeightM({}, 20)).toBeCloseTo(6);
        expect(roofHeightM({ 'roof:height': '4 m' }, 20)).toBe(4);
        expect(roofHeightM({ 'roof:height': '40' }, 20)).toBe(10);
    });

    it('keeps flat, missing and unknown shapes as the flat prism, byte for byte', () => {
        const base = buildingPrisms([house({})], flat());
        for (const shape of ['flat', 'unknown', 'dome']) {
            const g = buildingPrisms([house({ 'roof:shape': shape, 'roof:height': 5 })], flat());
            expect(Array.from(g.positions)).toEqual(Array.from(base.positions));
            expect(Array.from(g.indices)).toEqual(Array.from(base.indices));
        }
    });

    it('draws a gable: eave 0.6 mm below the top, ridge at the top along the long axis', () => {
        const g = buildingPrisms([house({ 'roof:shape': 'gabled' })], flat());
        expect(g.count).toBe(1);
        expect(maxY(g)).toBeCloseTo(2);                    // the ridge reaches the flat top
        const roofYs = new Set(ys(g).map((y) => y.toFixed(3)));
        expect(roofYs.has('1.400')).toBe(true);            // eave = 2 - 6 m x 0.1
        // ridge vertices sit mid-width (y = 1 px x 2 mm) at the top
        const p = g.positions;
        const ridge = [];
        for (let i = 0; i < p.length; i += 3) if (Math.abs(p[i + 1] - 2) < 1e-6) ridge.push(p[i + 2]);
        expect(ridge.length).toBeGreaterThan(0);
        for (const z of ridge) expect(z).toBeCloseTo(2);
        for (const idx of g.indices) expect(idx).toBeLessThan(p.length / 3);
    });

    it('draws a hip as a pyramid with its apex at the top', () => {
        const g = buildingPrisms([house({ 'roof:shape': 'hipped', 'roof:height': '4' })], flat());
        expect(maxY(g)).toBeCloseTo(2);
        const apex = [];
        for (let i = 0; i < g.positions.length; i += 3) {
            if (Math.abs(g.positions[i + 1] - 2) < 1e-6) apex.push([g.positions[i], g.positions[i + 2]]);
        }
        expect(apex).toEqual([[4, 2]]);                    // centroid (2, 1) px x 2 mm
    });

    it('leaves a roof under 0.2 mm flat', () => {
        const g = buildingPrisms([house({ 'roof:shape': 'gabled', 'roof:height': '1' })], flat());   // 0.1 mm
        const base = buildingPrisms([house({})], flat());
        expect(g.positions.length).toBe(base.positions.length);
    });
});

describe('wall foot on a slope', () => {
    // Corners at x=1 and x=3 are high (5 mm); a dip between them is 0 mm.
    const dip = (x) => (Math.abs(x - 2) < 0.4 ? 0 : 5);

    it('reaches a dip between the corners (edge midpoint and interior)', () => {
        const g = buildingPrisms([square(30)], opts(dip));
        expect(Math.min(...ys(g))).toBeCloseTo(0 - WALL_SINK_MM);
        expect(Math.max(...ys(g))).toBeCloseTo(5 + 3);             // highest ground + height
    });

    it('groundRange samples edges and interior, not only corners', () => {
        const ring = [[0, 0], [10, 0], [10, 10], [0, 10]];
        const pit = (x, y) => (Math.abs(x - 5) < 0.6 && Math.abs(y - 5) < 0.6 ? -7 : 1);
        expect(groundRange(ring, pit)).toEqual({ low: -7, high: 1 });
        const edgePit = (x, y) => (y === 0 && Math.abs(x - 5) < 0.6 ? -3 : 1);
        expect(groundRange(ring, edgePit).low).toBe(-3);
    });

    it('stays cheap for a large batch', () => {
        const feats = Array.from({ length: 24000 }, (_, i) => ({ properties: { height_m: 10 },
            geometry: { type: 'Polygon', coordinates: [[[i % 500, 0], [i % 500 + 4, 0], [i % 500 + 4, 4], [i % 500, 4], [i % 500, 0]]] } }));
        const t = performance.now();
        buildingPrisms(feats, opts(() => 1));
        expect(performance.now() - t).toBeLessThan(5000);
    });
});
