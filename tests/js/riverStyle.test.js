import { describe, it, expect } from 'vitest';
import {
    RIVER_RGB, OPEN_WATER_RGB, orderColor, riverPixelColor, riverEdgeMask, riverLineWidthPx, rgb,
} from '../../app/client/static/js/modules/layers/river-style.js';

describe('riverPixelColor', () => {
    it('draws nothing where there is no river', () => {
        expect(riverPixelColor(0, -1, null, 255, false)).toBeNull();
    });
    it('is light cyan by depth, deeper more opaque', () => {
        const shallow = riverPixelColor(-0.1, -1, null, 255, false);
        const deep = riverPixelColor(-1, -1, null, 255, false);
        expect(shallow.slice(0, 3)).toEqual([...RIVER_RGB]);
        expect(shallow[3]).toBeGreaterThanOrEqual(200);
        expect(deep[3]).toBe(255);
    });
    it('keeps open water its own colour', () => {
        expect(riverPixelColor(-1, -1, 255, 255, false).slice(0, 3)).toEqual([...OPEN_WATER_RGB]);
    });
    it('colours by order when asked', () => {
        expect(riverPixelColor(-1, -1, 4, 255, true).slice(0, 3)).toEqual([...orderColor(4)]);
        expect(riverPixelColor(-1, -1, 0, 255, true)).toBeNull();
    });
});

describe('riverEdgeMask', () => {
    it('marks the four neighbours of a one-pixel river, not the river', () => {
        const m = new Uint8Array(9); m[4] = 1;
        expect([...riverEdgeMask(m, 3, 3)]).toEqual([0, 1, 0, 1, 0, 1, 0, 1, 0]);
    });
});

describe('riverLineWidthPx', () => {
    it('is at least 1 px and grows with order and zoom', () => {
        expect(riverLineWidthPx(1)).toBeGreaterThanOrEqual(1);
        expect(riverLineWidthPx(8)).toBeGreaterThan(riverLineWidthPx(3));
        expect(riverLineWidthPx(5, 4)).toBeCloseTo(riverLineWidthPx(5, 1) * 2);
    });
    it('clamps the order table', () => {
        expect(orderColor(0)).toEqual(orderColor(1));
        expect(orderColor(99)).toEqual(orderColor(10));
        expect(rgb([1, 2, 3])).toBe('rgb(1, 2, 3)');
    });
});
