import { describe, it, expect } from 'vitest';
import {
    bboxSizeKm, formatBboxSize, parseBbox, regionBoxStyle, regionHaloStyle, REGION_ACCENT,
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

    it('highlights the selected region in the accent colour', () => {
        const s = regionBoxStyle('selected');
        expect(s.color).toBe(REGION_ACCENT);
        expect(s.fillOpacity).toBeCloseTo(0.08);
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
