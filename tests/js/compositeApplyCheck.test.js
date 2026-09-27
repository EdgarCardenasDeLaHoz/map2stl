/**
 * compositeApplyCheck.test.js — the Apply to DEM guard
 * (app/client/static/js/modules/layers/composite-spec.js).
 *
 * Bug: Apply used the last *finished* composite, which could be the all-zero
 * baseline computed before the DEM loaded, so the DEM became flat zeros.
 * Apply must only use a composite computed for the current inputs.
 */
import { describe, it, expect } from 'vitest';
import {
    compositeInputKey, compositeApplyCheck,
} from '../../app/client/static/js/modules/layers/composite-spec.js';

const BBOX = { north: 37.2, south: 37.1, east: -3.5, west: -3.7 };
const PARAMS = { demEnabled: true, demWeight: 1, waterEnabled: true, waterDepth: 5 };

function key(over = {}) {
    return compositeInputKey({ demToken: 1, width: 600, height: 400, bbox: BBOX, params: PARAMS, ...over });
}

describe('compositeInputKey', () => {
    it('is stable for equal inputs regardless of param key order', () => {
        const reordered = { waterDepth: 5, waterEnabled: true, demWeight: 1, demEnabled: true };
        expect(key()).toBe(key({ params: reordered }));
    });

    it('changes with the DEM, grid, bbox or any parameter', () => {
        const base = key();
        expect(key({ demToken: 2 })).not.toBe(base);
        expect(key({ demToken: null })).not.toBe(base);
        expect(key({ width: 601 })).not.toBe(base);
        expect(key({ bbox: { ...BBOX, north: 37.3 } })).not.toBe(base);
        expect(key({ params: { ...PARAMS, demWeight: 0.5 } })).not.toBe(base);
    });
});

describe('compositeApplyCheck', () => {
    const current = { key: key(), hasDem: true };
    const good = { key: key(), usedDem: true, min: 650, max: 3400 };

    it('accepts a result computed for the current DEM and settings', () => {
        expect(compositeApplyCheck(good, current)).toEqual({ ok: true });
    });

    it('refuses without a loaded DEM', () => {
        expect(compositeApplyCheck(good, { key: key(), hasDem: false }).reason).toBe('no-dem');
    });

    it('refuses when nothing was computed', () => {
        expect(compositeApplyCheck(null, current).reason).toBe('not-computed');
    });

    it('refuses the zero baseline computed before the DEM loaded', () => {
        // Baseline: no DEM token, #paramDim square grid, all zeros.
        const baseline = {
            key: key({ demToken: null, width: 200, height: 200 }), usedDem: false, min: 0, max: 0,
        };
        expect(compositeApplyCheck(baseline, current).reason).toBe('stale');
    });

    it('refuses a result from other panel settings', () => {
        const old = { ...good, key: key({ params: { ...PARAMS, waterDepth: 10 } }) };
        expect(compositeApplyCheck(old, current).reason).toBe('stale');
    });

    it('refuses a flat or empty result', () => {
        expect(compositeApplyCheck({ ...good, min: 0, max: 0 }, current).reason).toBe('flat');
        expect(compositeApplyCheck({ ...good, min: Infinity, max: -Infinity }, current).reason).toBe('flat');
    });
});
