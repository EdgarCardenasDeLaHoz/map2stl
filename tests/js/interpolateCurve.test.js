import { describe, it, expect } from 'vitest';
import { interpolateCurve } from './helpers/interpolateCurve.js';

describe('interpolateCurve', () => {
    const linear = [{ x: 0, y: 0 }, { x: 1, y: 1 }];
    const boosted = [{ x: 0, y: 0 }, { x: 0.5, y: 0.8 }, { x: 1, y: 1 }];

    it('returns x unchanged when fewer than 2 points', () => {
        expect(interpolateCurve(0.5, [])).toBe(0.5);
        expect(interpolateCurve(0.7, [{ x: 0, y: 0 }])).toBe(0.7);
    });

    it('identity curve: y = x for all x', () => {
        expect(interpolateCurve(0,   linear)).toBeCloseTo(0);
        expect(interpolateCurve(0.5, linear)).toBeCloseTo(0.5);
        expect(interpolateCurve(1,   linear)).toBeCloseTo(1);
    });

    it('clamps to endpoints for x outside the curve range', () => {
        // x < first point: uses left=right=first two points; t=(x-left.x)/(right.x-left.x)
        // x=0 with first point at 0 → t=0 → left.y
        expect(interpolateCurve(-0.1, linear)).toBeCloseTo(-0.1); // extrapolates linearly
        expect(interpolateCurve(1.1,  linear)).toBeCloseTo(1.1);
    });

    it('interpolates between segments correctly', () => {
        // boosted: 0→0, 0.5→0.8, 1→1
        // at x=0.25 (midpoint of first segment): y = lerp(0, 0.8, 0.5) = 0.4
        expect(interpolateCurve(0.25, boosted)).toBeCloseTo(0.4);
        // at x=0.75 (midpoint of second segment): y = lerp(0.8, 1, 0.5) = 0.9
        expect(interpolateCurve(0.75, boosted)).toBeCloseTo(0.9);
    });

    it('returns exact values at knot points', () => {
        expect(interpolateCurve(0,   boosted)).toBeCloseTo(0);
        expect(interpolateCurve(0.5, boosted)).toBeCloseTo(0.8);
        expect(interpolateCurve(1,   boosted)).toBeCloseTo(1);
    });

    it('handles zero-width segment without dividing by zero', () => {
        const degenerate = [{ x: 0.5, y: 0.3 }, { x: 0.5, y: 0.7 }];
        // right.x - left.x = 0, so divisor clamps to 1; t = (x-0.5)/1
        expect(() => interpolateCurve(0.5, degenerate)).not.toThrow();
    });
});
