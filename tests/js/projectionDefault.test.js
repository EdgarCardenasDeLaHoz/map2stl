// Default projection for a region without saved settings (modules/dem/projection-default.js).
import { describe, it, expect } from 'vitest';
import {
    LARGE_PROJECTION_REGION_KM, defaultProjectionFor,
} from '../../app/client/static/js/modules/dem/projection-default.js';

describe('defaultProjectionFor', () => {
    it('names the threshold as 1000 km', () => {
        expect(LARGE_PROJECTION_REGION_KM).toBe(1000);
    });

    it('picks laea for a Europe-sized box', () => {
        expect(defaultProjectionFor({ north: 68.65, south: 27.83, east: 47.28, west: -16.34 })).toBe('laea');
    });

    it('keeps cosine for a city or a region under 1000 km', () => {
        expect(defaultProjectionFor({ north: 37.2, south: 37.1, east: -3.5, west: -3.7 })).toBe('cosine');
        expect(defaultProjectionFor({ north: 44, south: 38, east: 0, west: -6 })).toBe('cosine');
    });

    it('reads string coordinates and survives a missing box', () => {
        expect(defaultProjectionFor({ north: '68', south: '28', east: '47', west: '-16' })).toBe('laea');
        expect(defaultProjectionFor(null)).toBe('cosine');
        expect(defaultProjectionFor({ north: 'x', south: 1, east: 2, west: 3 })).toBe('cosine');
    });
});
