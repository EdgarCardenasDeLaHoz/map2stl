/**
 * explore/notesMigration.ts - region notes moved from browser localStorage to the server
 * (user decision 2026-11-13). The rule: when the server's note for a region is empty and
 * this browser has one, upload it once; localStorage then stays only as a fallback copy.
 */
export const NOTES_KEY = 'map2stl_regionNotes';

type StorageLike = { getItem(k: string): string | null; setItem(k: string, v: string): void };

/** The browser-local notes {regionName: text}; {} when absent or unreadable. */
export function readLocalNotes(storage?: StorageLike | null): Record<string, string> {
    try {
        const s = storage ?? (typeof localStorage === 'undefined' ? null : localStorage);
        const raw = JSON.parse(s?.getItem(NOTES_KEY) || '{}');
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
        const out: Record<string, string> = {};
        for (const [k, v] of Object.entries(raw)) if (typeof v === 'string' && v.trim()) out[k] = v;
        return out;
    } catch { return {}; }
}

/** Keep a copy of the note in this browser (fallback when the server is unreachable). */
export function writeLocalNote(name: string, text: string, storage?: StorageLike | null): void {
    try {
        const s = storage ?? (typeof localStorage === 'undefined' ? null : localStorage);
        if (!s) return;
        const all = readLocalNotes(s);
        if (text.trim()) all[name] = text; else delete all[name];
        s.setItem(NOTES_KEY, JSON.stringify(all));
    } catch { /* storage full or blocked: the server copy is the one that counts */ }
}

/** Move a local note to a new region name (after a rename). */
export function renameLocalNote(oldName: string, newName: string, storage?: StorageLike | null): void {
    if (oldName === newName) return;
    const all = readLocalNotes(storage);
    if (!(oldName in all)) return;
    writeLocalNote(newName, all[oldName], storage);
    writeLocalNote(oldName, '', storage);
}

export interface NoteUpload { name: string; notes: string }

/** Which local notes to upload: the region exists, its server note is empty, a local one is not. */
export function planNotesMigration(
    regions: Array<{ name: string; notes?: string | null }>,
    local: Record<string, string>,
): NoteUpload[] {
    const out: NoteUpload[] = [];
    for (const r of regions) {
        const mine = (local[r.name] ?? '').trim();
        if (mine && !(r.notes ?? '').trim()) out.push({ name: r.name, notes: mine });
    }
    return out;
}

/** The note to show: the server's, else this browser's fallback copy. */
export function noteFor(region: { name: string; notes?: string | null }, local: Record<string, string>): string {
    return (region.notes ?? '').trim() ? (region.notes as string) : (local[region.name] ?? '');
}
