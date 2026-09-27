/**
 * demSampling.test.js — wording of the DEM `source_resolution` block
 * (app/client/static/js/modules/dem/dem-sampling.js).
 */
import { describe, it, expect } from 'vitest';
import { describeDemSampling, UPSAMPLE_WARN } from '../../app/client/static/js/modules/dem/dem-sampling.js';

describe('describeDemSampling', () => {
    it('reads "~30 m → 165×165 real samples, upsampled to 600×600" and warns above 4×', () => {
        const out = describeDemSampling({
            native_resolution_m: 30, native_samples: [165, 165], grid: [600, 600], upsample: 3.64,
        });
        expect(out.text).toBe('~30 m → 165×165 real samples, upsampled to 600×600');
        expect(out.warn).toBe(false);
        expect(out.warning).toBe('');
    });

    it('warns when most of the grid is interpolated', () => {
        const out = describeDemSampling({
            native_resolution_m: 90, native_samples: [55, 110], grid: [600, 1200], upsample: 10.9,
        });
        expect(out.text).toBe('~90 m → 110×55 real samples, upsampled to 1200×600');
        expect(out.warn).toBe(true);
        expect(out.warning).toMatch(/^10\.9× upsampled/);
        expect(UPSAMPLE_WARN).toBe(4);
    });

    it('says downsampled when the source is finer than the grid', () => {
        const out = describeDemSampling({
            native_resolution_m: 30, native_samples: [3600, 3600], grid: [600, 600], upsample: 0.17,
        });
        expect(out.text).toMatch(/downsampled to 600×600$/);
        expect(out.warn).toBe(false);
    });

    it('returns null without data', () => {
        expect(describeDemSampling(null)).toBeNull();
        expect(describeDemSampling({})).toBeNull();
    });
});

describe('describeDemSampling advice', () => {
    it('suggests a 30 m source only when the source is coarser', () => {
        const base = { native_samples: [40, 40], grid: [600, 600], upsample: 15 };
        expect(describeDemSampling({ ...base, native_resolution_m: 90 }).warning).toMatch(/30 m source/);
        expect(describeDemSampling({ ...base, native_resolution_m: 30 }).warning).toMatch(/finest source/);
    });
});
