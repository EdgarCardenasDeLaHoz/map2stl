import { describe, it, expect } from 'vitest';
import { selectViewportRegions, VIEWPORT_REGION_LIMIT }
    from '../../app/client/static/js/modules/regions/viewport-regions.js';

/** A square box of `size` degrees centred on (lat, lon). */
const box = (name, lat, lon, size) => ({
    name, north: lat + size / 2, south: lat - size / 2, east: lon + size / 2, west: lon - size / 2,
});

// Granada (small), SierraNevada (bigger, contains Granada), and a far-away region.
const granada = box('Granada', 37.18, -3.6, 0.2);
const sierra = box('SierraNevada', 37.1, -3.3, 1.5);
const tokyo = box('Tokyo', 35.7, 139.7, 0.5);
const regions = [granada, sierra, tokyo];

const view = (lat, lon, size) => box('view', lat, lon, size);

describe('selectViewportRegions', () => {
    it('shows every region at the world view (null view), largest first', () => {
        const r = selectViewportRegions(regions, null);
        expect(r.regions.map((x) => x.name)).toEqual(['SierraNevada', 'Tokyo', 'Granada']);
        expect(r.inViewCount).toBe(3);
        expect(r.total).toBe(3);
    });

    it('drops regions outside the view', () => {
        const r = selectViewportRegions(regions, view(37, -3.4, 4));
        expect(r.regions.map((x) => x.name)).toEqual(['SierraNevada', 'Granada']);
    });

    it('drops a box bigger than the view; zooming in reveals the small one', () => {
        const r = selectViewportRegions(regions, view(37.18, -3.6, 0.5));
        expect(r.regions.map((x) => x.name)).toEqual(['Granada']);
        expect(r.inViewCount).toBe(1);
    });

    it('always includes the selected region, without counting it', () => {
        const r = selectViewportRegions(regions, view(37.18, -3.6, 0.5), 'SierraNevada');
        expect(r.regions.map((x) => x.name)).toEqual(['Granada', 'SierraNevada']);
        expect(r.inViewCount).toBe(1);
        expect(r.shownInView).toBe(1);
    });

    it('caps the set at the limit, keeping the largest', () => {
        const many = Array.from({ length: 30 }, (_, i) => box(`R${i}`, 0, i, 0.1 + i * 0.01));
        const r = selectViewportRegions(many, null);
        expect(r.regions).toHaveLength(VIEWPORT_REGION_LIMIT);
        expect(r.regions[0].name).toBe('R29');
        expect(r.inViewCount).toBe(30);
        expect(r.shownInView).toBe(VIEWPORT_REGION_LIMIT);
    });

    it('adds the selected region past the limit', () => {
        const many = Array.from({ length: 30 }, (_, i) => box(`R${i}`, 0, i, 0.1 + i * 0.01));
        const r = selectViewportRegions(many, null, 'R0');
        expect(r.regions).toHaveLength(VIEWPORT_REGION_LIMIT + 1);
        expect(r.regions.at(-1).name).toBe('R0');
    });

    it('matches regions across a wrapped view (west past -180)', () => {
        const r = selectViewportRegions([tokyo], { north: 40, south: 30, west: -225, east: -215 });
        expect(r.regions.map((x) => x.name)).toEqual(['Tokyo']);
    });
});
