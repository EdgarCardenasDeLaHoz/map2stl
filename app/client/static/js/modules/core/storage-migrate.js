/**
 * storage-migrate.js — carry browser-side settings across the strm2stl → map2stl rename.
 *
 * Presets, region notes, thumbnails and panel widths are stored under
 * `map2stl_*` keys; before the rename they were `strm2stl_*`. Imported first by
 * main.js so every module reads the migrated value. A key already present under
 * the new name wins; the old key is left in place (harmless, and lets an older
 * build still read it).
 */

export function migrateStorageKeys(storage, from = 'strm2stl_', to = 'map2stl_') {
    let moved = 0;
    try {
        for (let i = 0; i < storage.length; i++) {
            const key = storage.key(i);
            if (!key || !key.startsWith(from)) continue;
            const target = to + key.slice(from.length);
            if (storage.getItem(target) === null) {
                storage.setItem(target, storage.getItem(key));
                moved++;
            }
        }
    } catch (_) { /* storage unavailable (private mode): nothing to migrate */ }
    return moved;
}

try { migrateStorageKeys(window.localStorage); } catch (_) { /* no localStorage */ }
