import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useRegionsStore, setRegionsApi } from '../../app/client/static/js/vue/stores/regions.ts';
import { useAppStore } from '../../app/client/static/js/vue/stores/app.ts';

const R = (name, lat, lon, extra = {}) => ({ name, north: lat + 1, south: lat - 1, east: lon + 1, west: lon - 1, ...extra });

function fakeApi(regions) {
    const db = regions.map((r) => ({ ...r }));
    return {
        db,
        calls: [],
        list: async () => ({ data: { regions: db.map((r) => ({ ...r })) }, error: null }),
        create: async (p) => { db.push({ ...p }); return { data: {}, error: null }; },
        update: async (name, p) => {
            const i = db.findIndex((r) => r.name === name);
            db[i] = { ...db[i], ...p };
            return { data: {}, error: null };
        },
        delete: async (name) => { db.splice(db.findIndex((r) => r.name === name), 1); return { data: {}, error: null }; },
        saveNotes: async (name, notes) => {
            const r = db.find((x) => x.name === name);
            if (!r) return { data: null, error: 'Not found' };
            r.notes = notes; return { data: {}, error: null };
        },
    };
}

let api;
beforeEach(() => {
    setActivePinia(createPinia());
    const mem = {};
    globalThis.localStorage = { getItem: (k) => mem[k] ?? null, setItem: (k, v) => { mem[k] = v; } };
    globalThis.window = { showToast: vi.fn(), setSelectedRegion: vi.fn(), selectCoordinate: vi.fn() };
    globalThis.confirm = () => true;
    api = fakeApi([R('Paris', 48.8, 2.3), R('Lima', -12, -77)]);
    setRegionsApi(api);
});

describe('regions store', () => {
    it('loads regions into the app store and lists them', async () => {
        const s = useRegionsStore();
        await s.load();
        expect(useAppStore().coordinatesData.map((r) => r.name)).toEqual(['Paris', 'Lima']);
        expect(s.loaded).toBe(true);
        expect(s.continents).toEqual(['South America', 'Europe']);
        expect(s.list.total).toBe(2);
    });

    it('uploads browser-local notes the server lacks (migration), once', async () => {
        localStorage.setItem('map2stl_regionNotes', JSON.stringify({ Paris: 'local note', Lima: 'x' }));
        api.db[1].notes = 'server note';
        const s = useRegionsStore();
        await s.load();
        await new Promise((r) => setTimeout(r));
        expect(api.db[0].notes).toBe('local note');
        expect(api.db[1].notes).toBe('server note');
    });

    it('select calls the legacy selectCoordinate with the index', async () => {
        const s = useRegionsStore();
        await s.load();
        await s.select('Lima');
        expect(window.selectCoordinate).toHaveBeenCalledWith(1, {});
    });

    it('update renames, saves notes, keeps the selection on the fresh object', async () => {
        const s = useRegionsStore();
        await s.load();
        useAppStore().selectedRegion = useAppStore().coordinatesData[0];
        s.editingName = 'Paris';
        const out = await s.update('Paris', { name: 'Paris 2', label: 'Europe', north: 50, south: 47, east: 4, west: 1 }, 'hello');
        expect(out).toBe('Paris 2');
        expect(s.editingName).toBe('Paris 2');
        expect(api.db[0].notes).toBe('hello');
        expect(useAppStore().selectedRegion.name).toBe('Paris 2');
        expect(useAppStore().selectedRegion).toBe(useAppStore().coordinatesData[0]);
    });

    it('remove clears selection and editor, honours the confirm', async () => {
        const s = useRegionsStore();
        await s.load();
        useAppStore().selectedRegion = useAppStore().coordinatesData[1];
        s.editingName = 'Lima';
        globalThis.confirm = () => false;
        expect(await s.remove('Lima')).toBe(false);
        globalThis.confirm = () => true;
        expect(await s.remove('Lima')).toBe(true);
        expect(useAppStore().selectedRegion).toBeNull();
        expect(s.editingName).toBeNull();
        expect(s.regions.map((r) => r.name)).toEqual(['Paris']);
    });

    it('search resets the page and bypasses the in-view scope', async () => {
        const s = useRegionsStore();
        await s.load();
        s.page = 3;
        s.setSearch('par');
        expect(s.page).toBe(0);
        expect(s.list.effectiveScope).toBe('all');
        expect(s.list.filtered).toBe(1);
    });

    it('mapRegions respects the Show boxes switch', async () => {
        const s = useRegionsStore();
        await s.load();
        expect(s.mapRegions.length).toBe(2);
        s.boxesVisible = false;
        expect(s.mapRegions).toEqual([]);
    });

    it('surfaces a load error', async () => {
        const s = useRegionsStore();
        setRegionsApi({ ...api, list: async () => ({ data: null, error: 'boom' }) });
        await s.load();
        expect(s.error).toBe('boom');
    });
});
