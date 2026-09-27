/**
 * cityFetch.test.js — background city fetch client
 * (app/client/static/js/modules/layers/city-fetch.js), against a fake api.
 */
import { describe, it, expect } from 'vitest';
import {
    mirrorHost, runCityFetch, summarizeCityFetch,
} from '../../app/client/static/js/modules/layers/city-fetch.js';

const status = (st, states, extra = {}) => ({
    task_id: 't1', status: st,
    layers: Object.entries(states).map(([name, state]) => ({ name, state })),
    elapsed_s: 3.4, mirror: 'https://overpass.kumi.systems/api', ...extra,
});

function fakeApi(statuses, { result = { buildings: { features: [] } } } = {}) {
    const calls = [];
    const queue = [...statuses];
    return {
        calls,
        start: async (body) => { calls.push(['start', body]); return { data: queue.shift(), error: null }; },
        status: async (id) => { calls.push(['status', id]); return { data: queue.shift(), error: null }; },
        result: async (id) => { calls.push(['result', id]); return { data: result, error: null }; },
        cancel: async (id) => { calls.push(['cancel', id]); return { data: status('cancelled', {}), error: null }; },
    };
}

describe('summarizeCityFetch / mirrorHost', () => {
    it('counts finished layers and names the mirror host', () => {
        const s = status('running', { buildings: 'done', roads: 'fetching', walls: 'cached' });
        expect(summarizeCityFetch(s)).toBe('2/3 layers · 3 s · overpass.kumi.systems');
    });
    it('handles missing fields', () => {
        expect(summarizeCityFetch({})).toBe('0/0 layers');
        expect(mirrorHost(null)).toBe('');
    });
});

describe('runCityFetch', () => {
    it('polls until done, reports every status and returns the result', async () => {
        const api = fakeApi([
            status('running', { buildings: 'pending' }),
            status('running', { buildings: 'fetching' }),
            status('done', { buildings: 'done' }),
        ]);
        const seen = [];
        const out = await runCityFetch(api, { north: 1 }, { onStatus: s => seen.push(s.layers[0].state), intervalMs: 0 });
        expect(out.status).toBe('done');
        expect(out.data).toEqual({ buildings: { features: [] } });
        expect(seen).toEqual(['pending', 'fetching', 'done']);
        expect(api.calls.map(c => c[0])).toEqual(['start', 'status', 'status', 'result']);
    });

    it('returns the error of a failed task without asking for a result', async () => {
        const api = fakeApi([
            status('running', { buildings: 'fetching' }),
            status('error', { buildings: 'failed' }, { error: 'All mirrors failed' }),
        ]);
        const out = await runCityFetch(api, {}, { intervalMs: 0 });
        expect(out).toMatchObject({ status: 'error', error: 'All mirrors failed' });
        expect(api.calls.some(c => c[0] === 'result')).toBe(false);
    });

    it('cancels the server task when the signal aborts', async () => {
        const api = fakeApi([status('running', { buildings: 'fetching' })]);
        const ctrl = new AbortController();
        const seen = [];
        const p = runCityFetch(api, {}, { signal: ctrl.signal, intervalMs: 10_000, onStatus: s => seen.push(s.status) });
        ctrl.abort();
        const out = await p;
        expect(out.status).toBe('cancelled');
        expect(api.calls.at(-1)).toEqual(['cancel', 't1']);
        expect(seen.at(-1)).toBe('cancelled');
    });

    it('reports a start failure', async () => {
        const api = { start: async () => ({ data: null, error: 'HTTP 422: too large' }) };
        expect(await runCityFetch(api, {})).toEqual({ status: 'error', error: 'HTTP 422: too large' });
    });
});
