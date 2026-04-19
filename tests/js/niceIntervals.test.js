import { describe, it, expect } from 'vitest';
import { nicePixelInterval, niceGeoInterval } from './helpers/niceIntervals.js';

describe('nicePixelInterval', () => {
    it('returns a value such that totalPixels / result <= targetLines', () => {
        for (const [px, tgt] of [[500, 5], [1000, 10], [800, 4], [256, 8]]) {
            const interval = nicePixelInterval(px, tgt);
            expect(px / interval).toBeLessThanOrEqual(tgt);
        }
    });

    it('result is always a power-of-10 multiple (1×, 2×, 5×, 10×)', () => {
        // Any "nice" number divided by its nearest power-of-10 must be 1, 2, 5, or 10
        const niceMultiples = [1, 2, 5, 10];
        for (const px of [100, 500, 1024, 2000, 9999]) {
            const interval = nicePixelInterval(px, 5);
            const mag = Math.pow(10, Math.floor(Math.log10(interval)));
            const mult = Math.round(interval / mag);
            expect(niceMultiples).toContain(mult);
        }
    });

    it('500px / 5 lines → interval 100', () => {
        expect(nicePixelInterval(500, 5)).toBe(100);
    });

    it('1000px / 10 lines → interval 100', () => {
        expect(nicePixelInterval(1000, 10)).toBe(100);
    });

    it('100px / 4 lines → interval ≤ 25', () => {
        const interval = nicePixelInterval(100, 4);
        expect(100 / interval).toBeLessThanOrEqual(4);
    });
});

describe('niceGeoInterval', () => {
    it('returns a value from the candidate list', () => {
        const candidates = [0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 45, 90];
        for (const [px, ppd, tgt] of [[500, 100, 6], [1000, 10, 8], [200, 500, 5]]) {
            const interval = niceGeoInterval(px, ppd, tgt);
            expect(candidates).toContain(interval);
        }
    });

    it('result satisfies totalRange / interval <= targetLines', () => {
        for (const [px, ppd, tgt] of [[500, 100, 6], [800, 50, 4], [200, 20, 5]]) {
            const interval = niceGeoInterval(px, ppd, tgt);
            const totalRange = px / ppd;
            expect(totalRange / interval).toBeLessThanOrEqual(tgt);
        }
    });

    it('very small range → small interval (e.g. 0.01°)', () => {
        // 10px / 1000 ppd = 0.01°, target 5 lines → 0.01° interval works
        const interval = niceGeoInterval(10, 1000, 5);
        expect(interval).toBeLessThanOrEqual(0.1);
    });

    it('very large range → large interval (90°)', () => {
        // 10000px / 10 ppd = 1000°, no candidate satisfies → returns 90
        const interval = niceGeoInterval(10000, 10, 5);
        expect(interval).toBe(90);
    });

    it('500px at 100ppd, target 6 → 1° interval', () => {
        // totalRange = 500/100 = 5°; 5/1 = 5 <= 6 ✓
        expect(niceGeoInterval(500, 100, 6)).toBe(1);
    });
});
