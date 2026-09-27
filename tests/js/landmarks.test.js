/**
 * landmarks.test.js — landmark search helpers
 * (app/client/static/js/modules/map/landmarks.js).
 */
import { describe, it, expect } from 'vitest';
import {
    bboxContains, bboxKey, extendBboxToInclude, placeCaption, toBbox,
} from '../../app/client/static/js/modules/map/landmarks.js';

const BOX = { north: 37.18, south: 37.17, east: -3.59, west: -3.6 };

describe('toBbox', () => {
    it('accepts plain boxes and Leaflet-like bounds', () => {
        expect(toBbox(BOX)).toEqual(BOX);
        const bounds = { getNorth: () => 2, getSouth: () => 1, getEast: () => 4, getWest: () => 3 };
        expect(toBbox({ getBounds: () => bounds })).toEqual({ north: 2, south: 1, east: 4, west: 3 });
    });
    it('rejects missing or inverted boxes', () => {
        expect(toBbox(null)).toBeNull();
        expect(toBbox({ north: 1, south: 2, east: 1, west: 0 })).toBeNull();
        expect(toBbox({ name: 'x' })).toBeNull();
    });
});

describe('bboxKey', () => {
    it('ignores float noise below the rounding', () => {
        expect(bboxKey(BOX)).toBe(bboxKey({ ...BOX, north: 37.180001 }));
        expect(bboxKey(BOX)).not.toBe(bboxKey({ ...BOX, north: 37.181 }));
        expect(bboxKey(null)).toBe('');
    });
});

describe('extendBboxToInclude', () => {
    it('grows only the side the place is on, plus the margin', () => {
        const out = extendBboxToInclude(BOX, { lat: 37.175, lon: -3.585 }, { marginM: 100 });
        expect(out.north).toBe(BOX.north);
        expect(out.south).toBe(BOX.south);
        expect(out.west).toBe(BOX.west);
        // 100 m at 37.175 N is ~0.00113 deg of longitude.
        expect(out.east).toBeCloseTo(-3.585 + 0.00113, 4);
    });

    it('uses a small place extent, but only the point of a large one', () => {
        const small = { lat: 37.175, lon: -3.585,
            bbox: { north: 37.176, south: 37.174, east: -3.583, west: -3.587 } };
        expect(extendBboxToInclude(BOX, small, { marginM: 0 }).east).toBeCloseTo(-3.583, 6);
        const city = { lat: 37.175, lon: -3.585,
            bbox: { north: 37.3, south: 37.1, east: -3.4, west: -3.8 } };
        const out = extendBboxToInclude(BOX, city, { marginM: 0 });
        expect(out.east).toBeCloseTo(-3.585, 6);
        expect(out.north).toBe(BOX.north);
    });

    it('leaves a box that already contains the place (plus margin) unchanged', () => {
        expect(extendBboxToInclude(BOX, { lat: 37.175, lon: -3.595 })).toEqual(BOX);
    });

    it('returns null without a box or a place', () => {
        expect(extendBboxToInclude(null, { lat: 1, lon: 1 })).toBeNull();
        expect(extendBboxToInclude(BOX, { lat: 'x', lon: 1 })).toBeNull();
    });
});

describe('bboxContains / placeCaption', () => {
    it('tests containment', () => {
        expect(bboxContains(BOX, 37.175, -3.595)).toBe(true);
        expect(bboxContains(BOX, 37.175, -3.585)).toBe(false);
    });
    it('captions class and type, dropping "yes"', () => {
        expect(placeCaption({ class: 'tourism', type: 'attraction' })).toBe('tourism · attraction');
        expect(placeCaption({ class: 'building', type: 'yes' })).toBe('building');
    });
});
