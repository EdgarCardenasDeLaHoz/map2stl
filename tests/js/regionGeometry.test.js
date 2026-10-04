import { describe, it, expect } from 'vitest';
import {
    bboxSizeKm, formatBboxSize, parseBbox, regionBoxStyle, regionHaloStyle, regionColor, regionDrawOrder, REGION_PALETTE, formatBboxDims, boxAround, placeBox, POINT_PLACE_KM,
} from '../../app/client/static/js/modules/regions/region-geometry.js';

describe('bboxSizeKm', () => {
    it('measures a 1° box on the equator as ~111 × ~111 km', () => {
        const s = bboxSizeKm({ north: 0.5, south: -0.5, east: 0.5, west: -0.5 });
        expect(s.widthKm).toBeCloseTo(111.3, 0);
        expect(s.heightKm).toBeCloseTo(110.6, 0);
        expect(s.areaKm2).toBeCloseTo(s.widthKm * s.heightKm, 6);
    });

    it('shrinks width with latitude', () => {
        const s = bboxSizeKm({ north: 60.5, south: 59.5, east: 1, west: 0 });
        expect(s.widthKm).toBeCloseTo(111.32 * Math.cos(Math.PI / 3), 0);
    });

    it('wraps a box crossing the antimeridian', () => {
        const s = bboxSizeKm({ north: 1, south: 0, east: -179, west: 179 });
        expect(s.widthKm).toBeCloseTo(2 * 111.3, 0);
    });
});

describe('formatBboxSize', () => {
    it('uses one decimal below 100 and whole numbers above', () => {
        expect(formatBboxSize({ widthKm: 12.44, heightKm: 8.12, areaKm2: 101.0 }))
            .toBe('12.4 × 8.1 km · 101 km²');
    });

    it('groups thousands for large areas', () => {
        expect(formatBboxSize({ widthKm: 200, heightKm: 150, areaKm2: 30000 }))
            .toBe('200 × 150 km · 30,000 km²');
    });
});

describe('parseBbox', () => {
    it('parses strings', () => {
        expect(parseBbox('37.2', '37.1', '-3.5', '-3.7'))
            .toEqual({ north: 37.2, south: 37.1, east: -3.5, west: -3.7 });
    });

    it('rejects north <= south, out of range, or missing values', () => {
        expect(parseBbox(1, 1, 2, 1)).toBeNull();
        expect(parseBbox(91, 0, 1, 0)).toBeNull();
        expect(parseBbox(1, 0, 181, 0)).toBeNull();
        expect(parseBbox('', 0, 1, 0)).toBeNull();
    });
});

describe('regionBoxStyle', () => {
    it('draws outlines only, with an invisible but clickable fill', () => {
        const s = regionBoxStyle('normal');
        expect(s.fill).toBe(true);
        expect(s.fillOpacity).toBe(0);
        expect(s.weight).toBe(1.5);
    });

    it('outlines a region in its colour and tints only the selected one', () => {
        const c = '#ff6b6b';
        expect(regionBoxStyle('normal', c).color).toBe(c);
        expect(regionBoxStyle('normal', c).fillOpacity).toBe(0);
        const s = regionBoxStyle('selected', c);
        expect(s.color).toBe(c);
        expect(s.fillColor).toBe(c);
        expect(s.fillOpacity).toBeGreaterThan(0);
        expect(s.weight).toBeGreaterThan(regionBoxStyle('normal').weight);
    });

    it('brightens a hovered box', () => {
        expect(regionBoxStyle('hover').opacity).toBeGreaterThan(regionBoxStyle('normal').opacity);
    });

    it('draws a wider non-interactive halo under each box', () => {
        const h = regionHaloStyle('normal');
        expect(h.weight).toBe(regionBoxStyle('normal').weight + 2);
        expect(h.interactive).toBe(false);
        expect(h.fill).toBe(false);
    });
});

describe('formatBboxDims', () => {
    it('gives width × height, one decimal under 100 km', () => {
        expect(formatBboxDims({ north: 37.1873, south: 37.1693, east: -3.5867, west: -3.6093 })).toBe('2.0 × 2.0 km');
    });
    it('rounds large boxes to whole km, matching the region editor', () => {
        expect(formatBboxDims({ north: 15, south: -19, east: -45, west: -85 })).toBe('4,450 × 3,760 km');
    });
    it('is empty without a box', () => {
        expect(formatBboxDims(null)).toBe('');
    });
});

describe('boxAround / placeBox', () => {
    it('makes a box of the asked ground size', () => {
        const b = boxAround(45.83, 6.865, 12, 1.2);
        const s = bboxSizeKm(b);
        expect(s.widthKm).toBeCloseTo(12, 1);
        expect(s.heightKm).toBeCloseTo(10, 1);
    });
    it('uses a place outline at least 1 km across', () => {
        const town = { lat: 37.18, lon: -3.6, bbox: { north: 37.25, south: 37.12, east: -3.55, west: -3.68 } };
        expect(placeBox(town)).toEqual(town.bbox);
    });
    it('puts a bed-shaped box around a point place', () => {
        const peak = { lat: 45.8326, lon: 6.8652, bbox: { north: 45.8327, south: 45.8325, east: 6.8653, west: 6.8651 } };
        const s = bboxSizeKm(placeBox(peak, 1));
        expect(s.widthKm).toBeCloseTo(POINT_PLACE_KM, 1);
        expect(s.heightKm).toBeCloseTo(POINT_PLACE_KM, 1);
    });
});

describe('regionColor', () => {
    it('gives a region the same palette colour every time', () => {
        expect(regionColor('Granada')).toBe(regionColor('Granada'));
        expect(REGION_PALETTE).toContain(regionColor('Granada'));
        expect(REGION_PALETTE).toContain(regionColor(''));
    });

    it('spreads regions over the palette', () => {
        const names = ['Granada', 'Colombia', 'Colombia_2', 'Mexico', 'Amazon', 'Philadelphia', 'Paris', 'Lisbon'];
        expect(new Set(names.map(regionColor)).size).toBeGreaterThanOrEqual(5);
    });
});

describe('regionDrawOrder', () => {
    it('draws the largest first so smaller boxes sit on top', () => {
        const big = { name: 'big', north: 10, south: 0, east: 10, west: 0 };
        const mid = { name: 'mid', north: 5, south: 0, east: 5, west: 0 };
        const small = { name: 'small', north: 1, south: 0, east: 1, west: 0 };
        expect(regionDrawOrder([small, big, mid]).map((r) => r.name)).toEqual(['big', 'mid', 'small']);
    });
});
