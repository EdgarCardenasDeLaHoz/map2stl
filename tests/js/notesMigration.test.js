import { describe, it, expect } from 'vitest';
import {
    NOTES_KEY, readLocalNotes, writeLocalNote, renameLocalNote, planNotesMigration, noteFor,
} from '../../app/client/static/js/vue/explore/notesMigration.ts';

const fake = (init = {}) => {
    const m = { ...init };
    return { getItem: (k) => m[k] ?? null, setItem: (k, v) => { m[k] = v; }, m };
};

describe('notes migration', () => {
    it('reads only non-empty string notes', () => {
        const s = fake({ [NOTES_KEY]: JSON.stringify({ a: 'x', b: '  ', c: 3 }) });
        expect(readLocalNotes(s)).toEqual({ a: 'x' });
        expect(readLocalNotes(fake({ [NOTES_KEY]: '[1]' }))).toEqual({});
        expect(readLocalNotes(fake({ [NOTES_KEY]: '{bad' }))).toEqual({});
    });
    it('uploads only when the server note is empty', () => {
        const regions = [{ name: 'A', notes: '' }, { name: 'B', notes: 'server' }, { name: 'C' }, { name: 'D' }];
        const plan = planNotesMigration(regions, { A: 'a', B: 'b', C: 'c' });
        expect(plan).toEqual([{ name: 'A', notes: 'a' }, { name: 'C', notes: 'c' }]);
    });
    it('prefers the server note, falls back to local', () => {
        expect(noteFor({ name: 'A', notes: 's' }, { A: 'l' })).toBe('s');
        expect(noteFor({ name: 'A', notes: '' }, { A: 'l' })).toBe('l');
        expect(noteFor({ name: 'Z' }, {})).toBe('');
    });
    it('writes, clears and renames local copies', () => {
        const s = fake();
        writeLocalNote('A', 'hi', s);
        expect(readLocalNotes(s)).toEqual({ A: 'hi' });
        renameLocalNote('A', 'B', s);
        expect(readLocalNotes(s)).toEqual({ B: 'hi' });
        writeLocalNote('B', '', s);
        expect(readLocalNotes(s)).toEqual({});
    });
});
