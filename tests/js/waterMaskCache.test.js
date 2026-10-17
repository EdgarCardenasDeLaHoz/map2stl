import { describe, it, expect, beforeAll, beforeEach } from 'vitest';

// cache.js is a side-effect module that attaches window.waterMaskCache.
let cache;

beforeAll(async () => {
    globalThis.window = globalThis.window || {};
    await import('../../app/client/static/js/modules/core/cache.js');
    cache = globalThis.window.waterMaskCache;
});

beforeEach(() => {
    cache.memory.clear();
});

const box = { north: 37.2, south: 37.1, east: -3.5, west: -3.7 };
const key = (extra) => ({
    ...box, dim: 600, projection: 'cosine', maintain_dimensions: 'false',
    clip_valid_region: 'true', ...extra,
});
const water = {
    esa_values_b64: 'AAAA', esa_dtype: 'uint8', esa_dimensions: [2, 2], resolution_m: 10,
};

describe('waterMaskCache key', () => {
    it('misses when the size changes', () => {
        cache.set(key({ dataset: 'esa' }), water);
        expect(cache.get(key({ dataset: 'esa' }))).toBe(water);
        expect(cache.get(key({ dataset: 'esa', dim: 900 }))).toBeNull();
    });

    it('misses when maintain_dimensions or clipping changes', () => {
        cache.set(key({ dataset: 'esa' }), water);
        expect(cache.get(key({ dataset: 'esa', maintain_dimensions: 'true' }))).toBeNull();
        expect(cache.get(key({ dataset: 'esa', clip_valid_region: 'false' }))).toBeNull();
    });
});

describe('waterMaskCache.findEsa', () => {
    it('returns the ESA grid of a matching water mask, from either dataset', () => {
        cache.set(key({ dataset: 'jrc' }), water);
        expect(cache.findEsa(key())).toEqual({
            esa_values_b64: 'AAAA', esa_dtype: 'uint8', esa_dimensions: [2, 2],
            resolution_m: 10, from_water_mask: true,
        });
    });

    it('returns null for another projection or size', () => {
        cache.set(key({ dataset: 'esa' }), water);
        expect(cache.findEsa(key({ projection: 'mercator' }))).toBeNull();
        expect(cache.findEsa(key({ dim: 900 }))).toBeNull();
    });

    it('returns null when the response has no ESA grid', () => {
        cache.set(key({ dataset: 'esa' }), { water_mask_values_b64: 'AAAA' });
        expect(cache.findEsa(key())).toBeNull();
    });
});
