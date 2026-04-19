import { describe, it, expect } from 'vitest';
import { haversineDiagKm } from './helpers/haversineDiagKm.js';

describe('haversineDiagKm', () => {
    it('returns 0 for a zero-size bbox', () => {
        expect(haversineDiagKm(10, 10, 20, 20)).toBeCloseTo(0);
    });

    it('1° × 1° box near equator is ~157 km diagonal', () => {
        // At equator: 1° lat ≈ 111 km, 1° lon ≈ 111 km → diag = sqrt(111²+111²) ≈ 157 km
        const km = haversineDiagKm(1, 0, 1, 0);
        expect(km).toBeCloseTo(157.25, 0);
    });

    it('1° lat span only (no lon span) is ~111 km', () => {
        const km = haversineDiagKm(1, 0, 0, 0);
        expect(km).toBeCloseTo(111.19, 0);
    });

    it('1° lon span only at equator is ~111 km', () => {
        const km = haversineDiagKm(0, 0, 1, 0);
        expect(km).toBeCloseTo(111.19, 0);
    });

    it('1° lon span at 60° lat is ~55.6 km (cosine foreshortening)', () => {
        // cos(60°) = 0.5 → ~55.6 km
        const km = haversineDiagKm(60, 60, 1, 0);
        expect(km).toBeCloseTo(55.6, 0);
    });

    it('is always non-negative', () => {
        expect(haversineDiagKm(51, 50, 0, -1)).toBeGreaterThanOrEqual(0);
    });

    it('larger bbox gives larger result', () => {
        const small = haversineDiagKm(1, 0, 1, 0);
        const large = haversineDiagKm(2, 0, 2, 0);
        expect(large).toBeGreaterThan(small);
    });

    it('UK bounding box is approximately 1100 km diagonal', () => {
        // Rough UK bbox: 49–61°N, 2–13°W — diagonal ~1100 km
        const km = haversineDiagKm(61, 49, -2, -8);
        expect(km).toBeGreaterThan(700);
        expect(km).toBeLessThan(1500);
    });
});
