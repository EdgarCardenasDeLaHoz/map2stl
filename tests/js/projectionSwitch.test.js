// Projection switch on the Edit tab (modules/dem/projection-switch.js), and the browser's
// inverse projection (graticule.js geoAtFrac, which the preview warp uses) checked against
// pixels the server actually produced.
import { describe, it, expect } from 'vitest';
import { afterPaint, awaitNewestDemLoad } from '../../app/client/static/js/modules/dem/projection-switch.js';
import { geoAtFrac } from '../../app/client/static/js/modules/dem/graticule.js';

const EUROPA = { north: 68.65655, south: 27.83908, east: 47.28516, west: -16.34766 };

describe('geoAtFrac against geo2stl/projections.py::project_grid', () => {
    // Computed in Python on 2026-10-17: a 385 x 600 plate-carree raster whose pixels hold their
    // own latitude (and, a second one, longitude) was projected with
    // project_grid(arr, *EUROPA, p, clip_nans=False, target_dim=600). Each entry is
    // [row, col, lat, lon] read off the output raster of size [width, height].
    const SERVER = {
        cosine: [600, 578, [[144, 300, 58.4522, 15.5218], [289, 300, 48.2125, 15.5218], [433, 180, 38.0434, 2.7952], [231, 480, 52.3084, 34.6116], [520, 60, 31.8996, -9.9314]]],
        mercator: [573, 600, [[150, 286, 61.6626, 15.4688], [300, 286, 52.7043, 15.4688], [450, 171, 41.4309, 2.6968], [240, 458, 56.5521, 34.5712], [540, 57, 33.5183, -9.9642]]],
        equidistant: [600, 578, [[144, 300, 58.4522, 15.5218], [289, 300, 48.2125, 15.5218], [433, 180, 38.0434, 2.7952], [231, 480, 52.3084, 34.6116], [520, 60, 31.8996, -9.9314]]],
        lambert: [600, 251, [[62, 300, 54.6932, 15.5218], [125, 300, 44.3732, 15.5218], [188, 180, 35.636, 2.7952], [100, 480, 48.2146, 34.6116], [225, 60, 30.9378, -9.9314]]],
        miller: [600, 509, [[127, 300, 60.2663, 15.5218], [254, 300, 50.6774, 15.5218], [381, 180, 39.8468, 2.7952], [203, 480, 54.6799, 34.6116], [458, 60, 32.72, -9.9314]]],
        gall: [600, 567, [[141, 300, 59.725, 15.5218], [283, 300, 49.9062, 15.5218], [425, 180, 39.2366, 2.7952], [226, 480, 53.9527, 34.6116], [510, 60, 32.4784, -9.9314]]],
        sinusoidal: [600, 435, [[108, 300, 58.4726, 15.5585], [217, 300, 48.2478, 15.5392], [326, 180, 38.023, 1.2479], [174, 480, 52.2814, 43.1441], [391, 60, 31.9257, -10.9824]]],
        laea: [600, 466, [[116, 300, 60.3877, 15.5621], [233, 300, 49.4971, 15.5402], [349, 180, 37.7307, 1.4277], [186, 480, 50.812, 42.6591], [419, 60, 28.6775, -9.9441]]],
        lcc: [600, 457, [[114, 300, 60.5261, 15.5651], [228, 300, 49.5395, 15.5435], [342, 180, 37.4378, 1.0572], [182, 480, 50.7146, 43.7494], [411, 60, 28.2779, -9.8662]]],
    };
    // Under half a pixel on Europa at 600 (a row is 0.07-0.16 deg of latitude, a column 0.1 deg).
    const TOL = 0.05;

    for (const [projection, [W, H, points]] of Object.entries(SERVER)) {
        it(`${projection}: each output pixel centre maps back to the server's lat/lon`, () => {
            const inv = geoAtFrac(EUROPA, projection, false);
            for (const [r, c, lat, lon] of points) {
                const g = inv((c + 0.5) / W, (r + 0.5) / H);
                expect(Math.abs(g.lat - lat)).toBeLessThan(TOL);
                expect(Math.abs(g.lon - lon)).toBeLessThan(TOL);
            }
        });
    }
});

describe('afterPaint', () => {
    it('resolves one task after the next animation frame', async () => {
        const frames = [];
        const timers = [];
        const p = afterPaint(50, { raf: (f) => frames.push(f), setTimer: (f, ms) => timers.push([f, ms]) });
        let done = false;
        p.then(() => { done = true; });
        expect(frames).toHaveLength(1);
        frames[0]();                                     // the frame runs: schedules a 0 ms task
        const zero = timers.find(([, ms]) => ms === 0);
        zero[0]();
        await p;
        expect(done).toBe(true);
    });

    it('falls back to the timeout where frames never run (a hidden tab)', async () => {
        const timers = [];
        const p = afterPaint(50, { raf: () => {}, setTimer: (f, ms) => timers.push([f, ms]) });
        timers.find(([, ms]) => ms === 50)[0]();
        await expect(p).resolves.toBeUndefined();
    });
});

describe('awaitNewestDemLoad (stale and superseded responses)', () => {
    /** A stand-in for window.loadDEM: each call replaces _controller and _promise. */
    const makeLoader = () => {
        const loader = { calls: [] };
        loader.call = () => {
            if (loader._controller) loader._controller.abort();
            const controller = new AbortController();
            let resolve;
            const run = new Promise((r) => { resolve = r; });
            loader._controller = controller;
            loader._promise = run;
            loader.calls.push({ controller, resolve });
            return run;
        };
        return loader;
    };

    it('lands when its own load brings a DEM', async () => {
        const loader = makeLoader();
        const state = { dem: null };
        const first = loader.call();
        const out = awaitNewestDemLoad(first, loader._controller, {
            loader, hasDem: () => !!state.dem, isCurrent: () => true,
        });
        state.dem = {}; loader.calls[0].resolve();
        expect(await out).toBe('landed');
    });

    it('waits for a newer loadDEM that aborted its own (Detail change mid-switch)', async () => {
        const loader = makeLoader();
        const state = { dem: null };
        const first = loader.call();
        const out = awaitNewestDemLoad(first, loader._controller, {
            loader, hasDem: () => !!state.dem, isCurrent: () => true,
        });
        let settled = null;
        out.then((v) => { settled = v; });
        loader.call();                       // the Detail change: aborts the first request
        loader.calls[0].resolve();           // the aborted load returns with no DEM
        await Promise.resolve(); await Promise.resolve();
        expect(settled).toBeNull();          // the old code ended the preview here
        state.dem = {}; loader.calls[1].resolve();
        expect(await out).toBe('landed');
    });

    it('reports a failure, not a wait, when the user cancels (controller aborted, not replaced)', async () => {
        const loader = makeLoader();
        const first = loader.call();
        const out = awaitNewestDemLoad(first, loader._controller, {
            loader, hasDem: () => false, isCurrent: () => true,
        });
        loader._controller.abort();
        loader.calls[0].resolve();
        expect(await out).toBe('failed');
    });

    it('reports a failure when the newer load fails too', async () => {
        const loader = makeLoader();
        const first = loader.call();
        const out = awaitNewestDemLoad(first, loader._controller, {
            loader, hasDem: () => false, isCurrent: () => true,
        });
        loader.call();
        loader.calls[0].resolve();
        loader.calls[1].resolve();
        expect(await out).toBe('failed');
    });

    it('goes stale, and touches nothing, once a newer projection pick owns the map', async () => {
        const loader = makeLoader();
        const state = { dem: null, pick: 1 };
        const first = loader.call();
        const out = awaitNewestDemLoad(first, loader._controller, {
            loader, hasDem: () => !!state.dem, isCurrent: () => state.pick === 1,
        });
        state.pick = 2;                      // re-pick: its own loadDEM aborts ours
        loader.call();
        state.dem = {};                      // even a DEM landing must not count for pick 1
        loader.calls[0].resolve();
        expect(await out).toBe('stale');
    });

    it('tolerates a rejected load', async () => {
        const loader = makeLoader();
        loader.call();
        const out = awaitNewestDemLoad(Promise.reject(new Error('x')), loader._controller, {
            loader, hasDem: () => false, isCurrent: () => true,
        });
        expect(await out).toBe('failed');
    });
});
