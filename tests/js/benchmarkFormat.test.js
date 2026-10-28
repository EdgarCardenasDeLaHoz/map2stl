/**
 * benchmarkFormat.test.js — formatting and delta logic of the Benchmark page
 * (app/client/static/js/modules/ui/benchmark-format.js).
 */
import { describe, it, expect } from 'vitest';
import {
    fmtMeters, fmtSignedMeters, fmtShare, fmtCount, fmtStamp, deltaInfo, withinLabel,
    usageFraction, usageText, gapHint,
} from '../../app/client/static/js/modules/ui/benchmark-format.js';

describe('number formatting', () => {
    it('formats metres and dashes missing values', () => {
        expect(fmtMeters(5.62)).toBe('5.6 m');
        expect(fmtMeters(null)).toBe('—');
        expect(fmtMeters(undefined)).toBe('—');
    });
    it('signs bias with a real minus and no sign at zero', () => {
        expect(fmtSignedMeters(1.5)).toBe('+1.5 m');
        expect(fmtSignedMeters(-0.44)).toBe('−0.4 m');
        expect(fmtSignedMeters(0.01)).toBe('0.0 m');
        expect(fmtSignedMeters(NaN)).toBe('—');
    });
    it('formats shares and counts', () => {
        expect(fmtShare(0.671)).toBe('67 %');
        expect(fmtShare(null)).toBe('—');
        expect(fmtCount(1000)).toBe('1,000');
        expect(fmtCount(null)).toBe('—');
    });
    it('turns a stamp into a short date', () => {
        expect(fmtStamp('2026-10-23_2145')).toBe('Oct 23');
        expect(fmtStamp('2026-01-05')).toBe('Jan 5');
        expect(fmtStamp('')).toBe('—');
        expect(fmtStamp('weird')).toBe('weird');
    });
});

describe('deltaInfo', () => {
    it('a lower MAE is better (negative delta)', () => {
        expect(deltaInfo(-4.94, '2026-10-20_0900')).toEqual({ kind: 'better', text: '▼ 4.9 m vs Oct 20' });
    });
    it('a higher MAE is worse', () => {
        expect(deltaInfo(2, null)).toEqual({ kind: 'worse', text: '▲ 2.0 m' });
    });
    it('rounds to no change', () => {
        expect(deltaInfo(0.03, '2026-10-20').kind).toBe('same');
    });
    it('has nothing to say without a previous score', () => {
        expect(deltaInfo(null, null)).toEqual({ kind: 'none', text: '' });
    });
});

describe('labels and usage', () => {
    it('names the within column by its meaning', () => {
        expect(withinLabel('3m')).toBe('Within 3 m');
        expect(withinLabel('25pct')).toBe('Within 25 %');
    });
    it('usage bar fraction is clamped and null without a counter', () => {
        expect(usageFraction(55, 1000)).toBeCloseTo(0.055);
        expect(usageFraction(2000, 1000)).toBe(1);
        expect(usageFraction(null, 1000)).toBeNull();
        expect(usageText(55, 1000)).toBe('55 / 1,000');
    });
    it('describes what paid fetches would cost', () => {
        expect(gapHint({ buildings: 100, missing: 0, tile_fetches: 0 })).toMatch(/nothing to fetch/);
        expect(gapHint({ buildings: 1200, missing: 300, tile_fetches: 9 }))
            .toBe('300 of 1,200 buildings have no cached truth; measuring them takes about 9 3D Tiles fetches.');
        expect(gapHint(null)).toBe('');
    });
});
