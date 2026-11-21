import { describe, it, expect } from 'vitest';
import {
    normalizeContinent, resolveContinent, groupByContinent, continentOptions,
    categoryOf, categoryOptions, buildListModel, buildTableGroups, continentPool, metaLine, LIST_PAGE_SIZE,
} from '../../app/client/static/js/vue/explore/regionList.ts';

const box = (name, lat, lon, half = 1, label = '') =>
    ({ name, label, north: lat + half, south: lat - half, east: lon + half, west: lon - half });
const WORLD = { north: 85, south: -85, west: -180, east: 180 };
const base = { scope: 'all', search: '', continent: 'all', page: 0, view: WORLD, selectedName: null };

describe('continent helpers', () => {
    it('normalizes labels and ignores non-continent labels', () => {
        expect(normalizeContinent(' europe ')).toBe('Europe');
        expect(normalizeContinent('Alps')).toBe('');
        expect(normalizeContinent(null)).toBe('');
    });
    it('resolves by label first, then by position', () => {
        expect(resolveContinent(box('X', 48, 10, 1, 'Asia'))).toBe('Asia');
        expect(resolveContinent(box('Paris', 48.8, 2.3))).toBe('Europe');
    });
    it('groups in canonical order with names sorted', () => {
        const g = groupByContinent([box('Zed', 48, 10), box('Ant', 48, 11), box('Lima', -12, -77)]);
        expect(g.map((x) => x.continent)).toEqual(['South America', 'Europe']);
        expect(g[1].regions.map((r) => r.name)).toEqual(['Ant', 'Zed']);
    });
    it('lists only present continents', () => {
        expect(continentOptions([box('Paris', 48.8, 2.3)])).toEqual(['Europe']);
    });
    it('filters the pool by continent', () => {
        const rs = [box('Paris', 48.8, 2.3), box('Lima', -12, -77)];
        expect(continentPool(rs, 'Europe').map((r) => r.name)).toEqual(['Paris']);
        expect(continentPool(rs, 'all')).toHaveLength(2);
    });
    it('categories use label else continent', () => {
        expect(categoryOf(box('A', 48, 10, 1, 'Alps'))).toBe('Alps');
        expect(categoryOf(box('B', 48, 10))).toBe('Europe');
        expect(categoryOptions([box('A', 48, 10, 1, 'Alps'), box('B', 48, 10)])).toEqual(['Alps', 'Europe']);
    });
});

describe('buildListModel', () => {
    const many = Array.from({ length: 45 }, (_, i) => box(`R${String(i).padStart(2, '0')}`, 48, i * 2 - 40, 0.2));
    it('is empty with no regions', () => {
        expect(buildListModel({ ...base, regions: [] }).state).toBe('empty');
    });
    it('pages the All list by 20', () => {
        const m = buildListModel({ ...base, regions: many, page: 1 });
        expect(m.pages).toBe(3);
        expect(m.start).toBe(LIST_PAGE_SIZE);
        expect(m.groups[0].regions).toHaveLength(20);
        expect(buildListModel({ ...base, regions: many, page: 9 }).page).toBe(2);
    });
    it('search overrides the scope and hides the tabs', () => {
        const m = buildListModel({ ...base, regions: many, scope: 'view', search: 'r07' });
        expect(m.effectiveScope).toBe('all');
        expect(m.showScopeTabs).toBe(false);
        expect(m.filtered).toBe(1);
        expect(m.viewNames).toBeNull();
    });
    it('in-view scope uses the viewport set and reports no-view', () => {
        const rs = [box('Paris', 48.8, 2.3, 0.3)];
        const inV = buildListModel({ ...base, regions: rs, scope: 'view', view: { north: 50, south: 47, east: 4, west: 0 } });
        expect(inV.viewNames).toEqual(['Paris']);
        const out = buildListModel({ ...base, regions: rs, scope: 'view', view: { north: -10, south: -20, east: 4, west: 0 } });
        expect(out.state).toBe('no-view');
    });
    it('continent filter narrows the pool', () => {
        const rs = [box('Paris', 48.8, 2.3), box('Lima', -12, -77)];
        const m = buildListModel({ ...base, regions: rs, continent: 'Europe' });
        expect(m.filtered).toBe(1);
        expect(m.total).toBe(2);
    });
});

describe('buildTableGroups / metaLine', () => {
    const rs = [box('Paris', 48.8, 2.3), box('Lima', -12, -77, 1, 'Andes')];
    it('filters by name and category', () => {
        expect(buildTableGroups({ regions: rs, search: 'par', category: '' })[0].regions[0].name).toBe('Paris');
        expect(buildTableGroups({ regions: rs, search: '', category: 'Andes' })[0].regions[0].name).toBe('Lima');
        expect(buildTableGroups({ regions: rs, search: 'zzz', category: '' })).toEqual([]);
    });
    it('joins non-empty parts', () => {
        expect(metaLine(['a', '', null, 'b'])).toBe('a · b');
    });
});
