import { describe, it, expect } from 'vitest';
import { readBedPreference, bedAspect, BED_KEY } from '../../app/client/static/js/modules/map/bed-preference.js';

const store = (v) => ({ getItem: (k) => (k === BED_KEY ? v : null) });

describe('readBedPreference', () => {
    it('falls back to a square bed when nothing is saved', () => {
        const b = readBedPreference(store(null));
        expect([b.w, b.h]).toEqual([220, 220]);
    });
    it('reads a preset', () => {
        const b = readBedPreference(store(JSON.stringify({ value: '256x256' })));
        expect([b.w, b.h]).toEqual([256, 256]);
        const c = readBedPreference(store(JSON.stringify({ value: '350x250' })));
        expect([c.w, c.h]).toEqual([350, 250]);
    });
    it('reads a custom bed', () => {
        const b = readBedPreference(store(JSON.stringify({ value: 'custom', w: '300', h: '200' })));
        expect([b.w, b.h]).toEqual([300, 200]);
    });
    it('survives bad JSON', () => {
        expect(readBedPreference(store('{oops')).w).toBe(220);
    });
});

describe('bedAspect', () => {
    it('is width over height', () => expect(bedAspect({ w: 300, h: 150 })).toBe(2));
    it('is square for unusable beds', () => {
        expect(bedAspect(null)).toBe(1);
        expect(bedAspect({ w: 0, h: 5 })).toBe(1);
    });
});
