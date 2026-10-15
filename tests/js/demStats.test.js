// One-pass DEM statistics (modules/dem/dem-stats.js): range and histogram bins of the loaded
// elevations, used by dem-main.js _applyDemResult and dem-loader.js drawHistogram.
import { describe, it, expect } from 'vitest';
import { binCounts, finiteRange } from '../../app/client/static/js/modules/dem/dem-stats.js';

describe('finiteRange', () => {
    it('skips no-data values', () => {
        expect(finiteRange(new Float32Array([NaN, 3, -2, Infinity, 7]))).toEqual({ min: -2, max: 7, count: 3 });
        expect(finiteRange([null, 5, undefined])).toEqual({ min: 5, max: 5, count: 1 });
    });

    it('returns NaN bounds when nothing is finite', () => {
        const r = finiteRange([NaN, -Infinity]);
        expect(r.count).toBe(0);
        expect(Number.isNaN(r.min) && Number.isNaN(r.max)).toBe(true);
        expect(finiteRange([]).count).toBe(0);
    });
});

describe('binCounts', () => {
    it('counts each finite value once, the top value in the last bin', () => {
        expect(binCounts([0, 1, 2, 3, 4, NaN], 0, 4, 4)).toEqual([1, 1, 1, 2]);
    });

    it('clamps values outside the range and handles a flat DEM', () => {
        expect(binCounts([-5, 10], 0, 4, 2)).toEqual([1, 1]);
        expect(binCounts([7, 7, 7], 7, 7, 3)).toEqual([3, 0, 0]);
    });
});
