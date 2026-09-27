/**
 * exportPoll.test.js — export polling stall detection
 * (app/client/static/js/modules/export/export-poll.js).
 *
 * The client used to give up after a fixed 10 minutes, while a first city
 * build took 611 s. It now gives up only after a stall: no status change and
 * no heartbeat (`alive`) for EXPORT_STALL_TIMEOUT_MS.
 */
import { describe, it, expect } from 'vitest';
import {
    EXPORT_STALL_TIMEOUT_MS, createStallWatch, exportProgressText, formatElapsed,
} from '../../app/client/static/js/modules/export/export-poll.js';

const MIN = 60 * 1000;
const st = (progress, message, alive = false) => ({ status: 'running', progress, message, alive });

describe('createStallWatch', () => {
    it('stalls after 3 min without any change or heartbeat', () => {
        const w = createStallWatch(0);
        expect(w.observe(st(30, 'Building model...'), 1000).stalled).toBe(false);
        expect(w.observe(st(30, 'Building model...'), 1000 + 2 * MIN).stalled).toBe(false);
        const r = w.observe(st(30, 'Building model...'), 1000 + EXPORT_STALL_TIMEOUT_MS);
        expect(r.stalled).toBe(true);
    });

    it('keeps going for a 20 min build whose worker reports alive', () => {
        const w = createStallWatch(0);
        for (let t = 0; t <= 20 * MIN; t += 30 * 1000) {
            expect(w.observe(st(30, 'Building model...', true), t).stalled).toBe(false);
        }
    });

    it('a progress or message change resets the stall clock', () => {
        const w = createStallWatch(0);
        w.observe(st(5, 'Loading OSM layers...'), 0);
        expect(w.observe(st(25, 'Terrain stage...'), 2.5 * MIN).stalled).toBe(false);
        expect(w.observe(st(25, 'Terrain stage...'), 5 * MIN).stalled).toBe(false);
        expect(w.observe(st(25, 'Terrain stage...'), 5.6 * MIN).stalled).toBe(true);
    });

    it('failed polls (null) count toward the stall', () => {
        const w = createStallWatch(0);
        w.observe(st(10, 'x', true), 0);
        expect(w.observe(null, 2 * MIN).stalled).toBe(false);
        expect(w.observe(null, 3 * MIN).stalled).toBe(true);
    });
});

describe('formatElapsed', () => {
    it('formats seconds, minutes and hours', () => {
        expect(formatElapsed(45_400)).toBe('45 s');
        expect(formatElapsed(245_000)).toBe('4:05');
        expect(formatElapsed(611_000)).toBe('10:11');
        expect(formatElapsed(3_723_000)).toBe('1:02:03');
        expect(formatElapsed(undefined)).toBe('0 s');
    });

    it('builds the progress line', () => {
        expect(exportProgressText('Building model...', 65_000)).toBe('Building model... (1:05)');
        expect(exportProgressText('', 0)).toBe('Working... (0 s)');
    });
});
