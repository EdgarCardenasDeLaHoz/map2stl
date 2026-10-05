/**
 * modules/core/cache.js — Client-side water mask cache.
 *
 * Imported by main.js. Exposes:
 *   window.waterMaskCache  — cache for water mask API responses
 *     .get(bbox)           — return cached data or null
 *     .set(bbox, data)     — store data; evicts the oldest-inserted entry over maxSize
 *     .generateKey(bbox)   — return the string cache key for a bbox
 *     .dedupe(bbox, fetchFn) — share one in-flight request across concurrent
 *                              callers for the same key (see below)
 */

window.waterMaskCache = {
    memory: new Map(),
    maxSize: 50,
    // In-flight request promises, keyed the same way as `memory`. Lets two
    // callers that want the same water mask at (nearly) the same moment share
    // one network request instead of both paying for it independently — this
    // matters most when the request is slow AND failing (e.g. Earth Engine not
    // authenticated: ~3-4s per attempt), since a plain result-cache can't help
    // until the first call has actually finished.
    _inflight: new Map(),

    // Generate cache key from bbox, sat_scale, projection, and dataset.
    generateKey(bbox) {
        // sat_scale controls ESA data quality; demWidth/demHeight ensure alignment.
        // projection and dataset are included so that changing either param causes a
        // cache miss and forces a fresh server fetch.
        const sc = bbox.sat_scale || bbox.resolution || 0;
        const demW = bbox.demWidth || 0;
        const demH = bbox.demHeight || 0;
        const proj = bbox.projection || 'none';
        const ds = bbox.dataset || '';
        return `${bbox.north.toFixed(4)}_${bbox.south.toFixed(4)}_${bbox.east.toFixed(4)}_${bbox.west.toFixed(4)}_sc${sc}_${demW}x${demH}_${proj}_${ds}`;
    },

    get(bbox) {
        const key = this.generateKey(bbox);
        return this.memory.has(key) ? this.memory.get(key).data : null;
    },

    set(bbox, data) {
        const key = this.generateKey(bbox);
        this.memory.set(key, { data, timestamp: Date.now() });
        if (this.memory.size > this.maxSize) {
            const oldest = Array.from(this.memory.entries())
                .sort((a, b) => a[1].timestamp - b[1].timestamp)[0];
            this.memory.delete(oldest[0]);
        }
    },

    /**
     * Run `fetchFn()` for this bbox, sharing one in-flight promise across any
     * concurrent callers using the same key (regardless of success/failure).
     * `fetchFn` should return `{ data, error }` (the shape window.api.dem.*
     * helpers return) and is responsible for its own success-caching via
     * `.set()` if desired.
     */
    dedupe(bbox, fetchFn) {
        const key = this.generateKey(bbox);
        const existing = this._inflight.get(key);
        if (existing) return existing;

        const promise = Promise.resolve(fetchFn()).finally(() => {
            this._inflight.delete(key);
        });
        this._inflight.set(key, promise);
        return promise;
    },
};
