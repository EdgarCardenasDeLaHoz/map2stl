import { describe, expect, it } from 'vitest';
import { migrateStorageKeys } from '../../app/client/static/js/modules/core/storage-migrate.js';

function memoryStorage(init = {}) {
    const m = new Map(Object.entries(init));
    return {
        get length() { return m.size; },
        key: (i) => [...m.keys()][i] ?? null,
        getItem: (k) => (m.has(k) ? m.get(k) : null),
        setItem: (k, v) => m.set(k, String(v)),
        _map: m,
    };
}

describe('migrateStorageKeys', () => {
    it('copies strm2stl_* keys to map2stl_* without overwriting newer values', () => {
        const s = memoryStorage({ strm2stl_userPresets: '[1]', strm2stl_autoSave: 'false',
                                  map2stl_autoSave: 'true', other: 'x' });
        expect(migrateStorageKeys(s)).toBe(1);
        expect(s.getItem('map2stl_userPresets')).toBe('[1]');
        expect(s.getItem('map2stl_autoSave')).toBe('true');
        expect(s.getItem('strm2stl_userPresets')).toBe('[1]');
    });
});
