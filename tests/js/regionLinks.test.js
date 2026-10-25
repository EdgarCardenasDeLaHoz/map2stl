/**
 * regionLinks.test.js — shortcut links to a saved region
 * (app/client/static/js/modules/regions/region-links.js): `#region=<name>&tab=<tab>`.
 */
import { describe, it, expect } from 'vitest';
import {
    TAB_VIEWS, parseRegionHash, regionHash, findRegionIndex,
} from '../../app/client/static/js/modules/regions/region-links.js';

describe('parseRegionHash', () => {
    it('reads the region and tab', () => {
        expect(parseRegionHash('#region=Granada&tab=edit')).toEqual({ region: 'Granada', tab: 'edit' });
        expect(parseRegionHash('#region=Miami%2C%20FL&tab=EXTRUDE'))
            .toEqual({ region: 'Miami, FL', tab: 'extrude' });
    });

    it('defaults to Explore for a missing or unknown tab', () => {
        expect(parseRegionHash('#region=Granada')).toEqual({ region: 'Granada', tab: 'explore' });
        expect(parseRegionHash('#region=Granada&tab=print')).toEqual({ region: 'Granada', tab: 'explore' });
    });

    it('returns null when no region is named', () => {
        for (const h of ['', '#', '#tab=edit', '#region=', '#region=%20', null, undefined]) {
            expect(parseRegionHash(h)).toBeNull();
        }
    });
});

describe('regionHash', () => {
    it('round-trips through parseRegionHash', () => {
        for (const name of ['Granada', 'Miami, FL (2)', 'Breckenridge+KEyStone', 'São Paulo & co']) {
            for (const tab of Object.keys(TAB_VIEWS)) {
                expect(parseRegionHash(regionHash(name, tab))).toEqual({ region: name, tab });
            }
        }
    });

    it('leaves Explore out of the link', () => {
        expect(regionHash('Granada')).toBe('#region=Granada');
        expect(regionHash('Granada', 'explore')).toBe('#region=Granada');
        expect(regionHash('San Fran', 'edit')).toBe('#region=San%20Fran&tab=edit');
    });
});

describe('findRegionIndex', () => {
    const regions = [{ name: 'San Fran' }, { name: 'SanFranciso' }, { name: 'Miami, FL' },
        { name: 'Miami, FL (2)' }, { name: 'granada' }, { name: 'Granada' }];

    it('prefers an exact match', () => {
        expect(findRegionIndex(regions, 'Granada')).toBe(5);
        expect(findRegionIndex(regions, 'Miami, FL (2)')).toBe(3);
    });

    it('falls back to ignoring case, spaces and punctuation', () => {
        expect(findRegionIndex(regions, 'san-fran')).toBe(0);
        expect(findRegionIndex(regions, 'MIAMI FL')).toBe(2);
        expect(findRegionIndex(regions, 'GRANADA')).toBe(4);
    });

    it('returns -1 for an unknown name', () => {
        expect(findRegionIndex(regions, 'Lisbon')).toBe(-1);
        expect(findRegionIndex(regions, '!!!')).toBe(-1);
        expect(findRegionIndex([], 'Granada')).toBe(-1);
    });
});
