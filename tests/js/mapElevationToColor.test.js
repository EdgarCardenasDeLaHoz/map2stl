import { describe, it, expect } from 'vitest';
import { mapElevationToColor } from './helpers/mapElevationToColor.js';

/** All RGB channels must be in [0, 1]. */
function inRange(rgb) {
    return rgb.every(v => v >= 0 && v <= 1);
}

describe('mapElevationToColor', () => {
    describe('input clamping', () => {
        it('clamps t below 0 to 0', () => {
            const below = mapElevationToColor(-0.5, 'gray');
            const zero  = mapElevationToColor(0,    'gray');
            expect(below).toEqual(zero);
        });

        it('clamps t above 1 to 1', () => {
            const above = mapElevationToColor(1.5, 'gray');
            const one   = mapElevationToColor(1,   'gray');
            expect(above).toEqual(one);
        });

        it('fixes "raindow" typo alias', () => {
            const typo   = mapElevationToColor(0.5, 'raindow');
            const correct = mapElevationToColor(0.5, 'rainbow');
            expect(typo).toEqual(correct);
        });
    });

    describe('gray colormap', () => {
        it('returns [t, t, t]', () => {
            expect(mapElevationToColor(0,   'gray')).toEqual([0, 0, 0]);
            expect(mapElevationToColor(0.5, 'gray')).toEqual([0.5, 0.5, 0.5]);
            expect(mapElevationToColor(1,   'gray')).toEqual([1, 1, 1]);
        });
    });

    describe('hot colormap', () => {
        it('starts black and ends white', () => {
            expect(mapElevationToColor(0, 'hot')).toEqual([0, 0, 0]);
            expect(mapElevationToColor(1, 'hot')).toEqual([1, 1, 1]);
        });

        it('is monotonically increasing in R', () => {
            const r = [0, 0.2, 0.4, 0.6, 0.8, 1].map(t => mapElevationToColor(t, 'hot')[0]);
            for (let i = 1; i < r.length; i++) expect(r[i]).toBeGreaterThanOrEqual(r[i - 1]);
        });

        it('all values in [0, 1]', () => {
            [0, 0.25, 0.5, 0.75, 1].forEach(t => expect(inRange(mapElevationToColor(t, 'hot'))).toBe(true));
        });
    });

    describe('jet colormap', () => {
        it('all values in [0, 1]', () => {
            [0, 0.25, 0.5, 0.75, 1].forEach(t => expect(inRange(mapElevationToColor(t, 'jet'))).toBe(true));
        });

        it('is blue at t=0 and t=1', () => {
            // jet: at t=0 → mostly blue; at t=1 → mostly red
            const [r0, , b0] = mapElevationToColor(0, 'jet');
            const [r1, , b1] = mapElevationToColor(1, 'jet');
            expect(b0).toBeGreaterThan(r0);
            expect(r1).toBeGreaterThan(b1);
        });
    });

    describe('rainbow colormap', () => {
        it('all values in [0, 1]', () => {
            [0, 0.25, 0.5, 0.75, 1].forEach(t => expect(inRange(mapElevationToColor(t, 'rainbow'))).toBe(true));
        });
    });

    describe('viridis colormap', () => {
        it('all values in [0, 1]', () => {
            [0, 0.25, 0.5, 0.75, 1].forEach(t => expect(inRange(mapElevationToColor(t, 'viridis'))).toBe(true));
        });
    });

    describe('default (terrain) colormap', () => {
        it('all values in [0, 1]', () => {
            [0, 0.2, 0.4, 0.6, 0.8, 1].forEach(t =>
                expect(inRange(mapElevationToColor(t, 'terrain'))).toBe(true));
        });

        it('returns an array of 3 numbers', () => {
            const rgb = mapElevationToColor(0.5, 'terrain');
            expect(rgb).toHaveLength(3);
            rgb.forEach(v => expect(typeof v).toBe('number'));
        });
    });
});
