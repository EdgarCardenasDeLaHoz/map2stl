/**
 * cityQuickview.test.js — buildings on the Extrude preview
 * (app/client/static/js/modules/export/city-quickview.js).
 */
import { describe, it, expect } from 'vitest';
import { buildingPrisms, demGroundMm, MIN_HEIGHT_MM } from '../../app/client/static/js/modules/export/city-quickview.js';

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
        expect(Math.min(...ys(g))).toBeCloseTo(1 - 0.1);          // walls reach the lowest ground
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
